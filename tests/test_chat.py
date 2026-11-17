"""The chat-first MindChart: the psychiatric NP agent's tools and conversation, saved chats, answers written as jobs,
and the AI (Ollama) started only when it's needed. A stand-in Ollama answers, so no AI model is needed."""
import http.client
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import support
import agent
import ai
import history
import server
import vault


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Tools(unittest.TestCase):
    def test_ages_and_time_between_dates_are_exact(self):
        self.assertEqual(agent.run_tool("calculate_age", {"date_of_birth": "05/14/2011", "on_date": "05/13/2026"}),
                         {"age_years": 14, "and_months": 11, "on": "05/13/2026"})  # the day before the birthday
        self.assertEqual(agent.run_tool("calculate_age", {"date_of_birth": "02/29/2012", "on_date": "02/28/2026"})["age_years"], 13)
        between = agent.run_tool("time_between", {"start_date": "01/31/2026", "end_date": "03/01/2026"})
        self.assertEqual((between["months"], between["days"], between["total_days"]), (1, 1, 29))
        self.assertIn("error", agent.run_tool("calculate_age", {"date_of_birth": "sometime"}))
        self.assertIn("error", agent.run_tool("calculate_age", {"date_of_birth": "05/14/2011", "on_date": "01/01/2010"}))

    def test_rating_scales_use_their_published_bands(self):
        self.assertEqual(agent.run_tool("score_rating_scale", {"scale": "GAD-7", "total": 12}), {"scale": "GAD-7", "total": 12, "band": "Moderate"})
        phq = agent.run_tool("score_rating_scale", {"scale": "phq-a", "item_scores": [1, 2, 1, 2, 1, 1, 1, 0, 1]})
        self.assertEqual((phq["total"], phq["band"]), (10, "Moderate"))
        self.assertIn("suicide risk", phq["warning"])  # item 9 above 0
        self.assertIn("error", agent.run_tool("score_rating_scale", {"scale": "PHQ-A", "item_scores": [1, 2]}))
        self.assertIn("error", agent.run_tool("score_rating_scale", {"scale": "Beck", "total": 3}))
        self.assertIn("error", agent.run_tool("no_such_tool", {}))


class SafetyNet(unittest.TestCase):
    def test_scores_in_a_message_are_scored_before_the_ai_answers(self):
        scored = agent.score_scales("PHQ-A today, item scores 1,2,1,2,1,1,1,0,1. GAD-7 of 12.")
        self.assertEqual([(s["scale"], s["total"], s["band"]) for s in scored], [("PHQ-A", 10, "Moderate"), ("GAD-7", 12, "Moderate")])
        self.assertIn("suicide risk", scored[0]["warning"])
        self.assertEqual(agent.score_scales("PHQ-A on 05/14/2024 was 12"), [])  # a date isn't guessed at
        self.assertEqual(agent.score_scales("no scale, just 1,2,3"), [])
        messages = agent.model_messages([{"role": "user", "content": "PHQ-A 1,2,1,2,1,1,1,0,1", "scored": scored[:1]}])
        self.assertIn("[Scored by MindChart: PHQ-A total 10 (Moderate): Item 9", messages[1]["content"])


class Conversation(unittest.TestCase):
    def test_the_model_sees_its_role_the_date_preferences_and_files(self):
        messages = agent.model_messages([{"role": "user", "content": "Summarize this.", "files": [
            {"name": "notes.png", "text": "Pt calm, slp bttr", "unclear": [1]}]}], ["Refer to the patient as 'the youth'."])
        system, user = messages[0]["content"], messages[1]["content"]
        self.assertIn("psychiatric-mental health", system)
        self.assertIn("Never invent", system)
        self.assertIn(time.strftime("%Y"), system)
        self.assertIn("- Refer to the patient as 'the youth'.", system)
        self.assertIn('<file name="notes.png">', user)
        self.assertIn("misread", user)
        self.assertTrue(user.endswith("Summarize this."))

    def test_long_conversations_keep_the_newest_messages(self):
        history_ = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i} " + "x" * 1000} for i in range(30)]
        messages = agent.model_messages(history_, context_chars=5000)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")  # it never starts with an answer
        self.assertTrue(messages[-1]["content"].startswith("message 29"))
        self.assertLessEqual(len(messages), 6)
        big = agent.model_messages([{"role": "user", "content": "Read it", "files": [{"name": "a.pdf", "text": "y" * 100000}]}], context_chars=8000)
        self.assertIn("too long to include", big[1]["content"])
        self.assertLess(len(big[1]["content"]), 9000)


class FakeOllama(http.server.BaseHTTPRequestHandler):
    """Answers like Ollama's chat: the first request asks for a tool, the next answers with the tool's result in hand."""
    requests = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        if not any(m["role"] == "tool" for m in body["messages"]):
            lines = [{"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "calculate_age", "arguments": {"date_of_birth": "05/14/2011", "on_date": "11/16/2026"}}}]}, "done": True}]
        else:
            age = json.loads(next(m for m in body["messages"] if m["role"] == "tool")["content"])["age_years"]
            lines = [{"message": {"content": f"He is **{age}** "}}, {"message": {"content": "years old."}, "done": True}]
        for line in lines:
            self.wfile.write((json.dumps(line) + "\n").encode())


class Answering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.ollama = http.server.ThreadingHTTPServer(("127.0.0.1", cls.port), FakeOllama)
        threading.Thread(target=cls.ollama.serve_forever, daemon=True).start()
        cls.real_url, history.OLLAMA_URL = history.OLLAMA_URL, f"http://127.0.0.1:{cls.port}/api/chat"

    @classmethod
    def tearDownClass(cls):
        history.OLLAMA_URL = cls.real_url
        cls.ollama.shutdown()
        cls.ollama.server_close()

    def test_the_agent_uses_a_tool_then_answers(self):
        FakeOllama.requests = []
        streamed = []
        result = agent.reply("bonsai-2:9b", True, agent.model_messages([{"role": "user", "content": "How old is he? Born 05/14/2011."}]),
                             streamed.append, context_length=8192, max_tokens=2500)
        self.assertEqual(result, {"text": "He is **15** years old.", "tools": ["age calculator"], "stopped": False})
        self.assertEqual("".join(streamed), "He is **15** years old.")
        first = FakeOllama.requests[0]
        self.assertEqual(first["options"]["num_ctx"], 8192)  # an 8 GB Mac's smaller context
        self.assertIs(first["think"], False)  # thinking is off, for speed
        self.assertEqual({t["function"]["name"] for t in first["tools"]}, {"calculate_age", "time_between", "score_rating_scale"})


class Chats(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.unlocked_vault()

    def test_chats_are_kept_titled_renamed_and_deleted(self):
        uid = vault.create_chat()
        self.assertEqual(vault.get_chat(uid)["title"], "New chat")
        vault.add_chat_message(uid, {"role": "user", "content": "Help me write a safety plan for a 14-year-old after a self-harm episode",
                                     "files": []})
        vault.add_chat_message(uid, {"role": "assistant", "content": "Here's a draft.", "model": "m", "tools": []})
        chat = vault.get_chat(uid)
        self.assertEqual(chat["title"], "Help me write a safety plan for a 14-year-old after a…")
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "assistant"])
        self.assertEqual(vault.list_chats()[0]["id"], uid)  # most recently used first
        vault.rename_chat(uid, "Safety plan")
        self.assertEqual(vault.get_chat(uid)["title"], "Safety plan")
        with self.assertRaises(ValueError):
            vault.rename_chat(uid, "  ")
        vault.delete_chat(uid)
        with self.assertRaisesRegex(ValueError, "deleted"):
            vault.get_chat(uid)
        self.assertIn("deleted a chat", [a["action"] for a in vault.list_records("activity")["rows"][:3]])


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        support.unlocked_vault()
        cls.port = free_port()
        cls.httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", cls.port), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.real = (server.agent.reply, server.model_choice)
        server.model_choice = lambda payload: (payload.get("model") or "bonsai-2:9b", True)  # (doesn't start the real Ollama)

    def tearDown(self):
        server.agent.reply, server.model_choice = self.real

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request(method, path, body=json.dumps(body) if body is not None else None,
                           headers={"Host": f"localhost:{server.PORT}", "X-MindChart": "1", "Content-Type": "application/json"})
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def wait_for_answer(self, uid):
        for _ in range(100):
            job = self.request("GET", f"/api/chats/{uid}/reply")[1]
            if job["done"]:
                return job
            time.sleep(0.05)
        self.fail("No answer")

    def test_a_message_is_answered_as_a_job_and_saved(self):
        seen = {}

        def reply(model, thinking, messages, on_text, should_stop=lambda: False, **sizes):
            seen.update(messages=messages, **sizes)
            for chunk in ("Consider ", "an SSRI."):
                on_text(chunk)
            return {"text": "Consider an SSRI.", "tools": ["rating scale scorer"], "stopped": False}
        server.agent.reply = reply
        self.assertIs(self.request("GET", "/api/session")[1]["records"], False)  # the records features are on hold
        uid = self.request("POST", "/api/chats", {})[1]["id"]
        status, _ = self.request("POST", f"/api/chats/{uid}/messages", {"text": "PHQ-A 12, what next?", "files": [
            {"name": "note.pdf", "text": "Seen today.", "unclear": []}], "model": "bonsai-2:9b"})
        self.assertEqual(status, 200)  # it answers straight away; the answer is written on its own
        job = self.wait_for_answer(uid)
        self.assertEqual((job["text"], job["error"], job["tools"]), ("Consider an SSRI.", None, ["rating scale scorer"]))
        self.assertIn('<file name="note.pdf">', seen["messages"][-1]["content"])
        self.assertEqual(seen["context_length"], server.CHAT_CONTEXT)
        chat = self.request("GET", f"/api/chats/{uid}")[1]
        self.assertEqual([(m["role"], m["content"]) for m in chat["messages"]],
                         [("user", "PHQ-A 12, what next?"), ("assistant", "Consider an SSRI.")])
        self.assertEqual(chat["messages"][1]["tools"], ["rating scale scorer"])
        self.assertEqual(chat["messages"][0]["scored"][0]["band"], "Moderate")  # the PHQ-A 12 in the message, scored by MindChart
        self.assertIsNone(chat["reply"])
        self.assertIn(uid, [c["id"] for c in self.request("GET", "/api/chats")[1]["chats"]])
        self.assertEqual(self.request("POST", f"/api/chats/{uid}/messages", {"text": " "})[0], 400)  # nothing to send
        self.assertEqual(self.request("POST", f"/api/chats/{uid}/delete", {})[0], 200)
        self.assertEqual(self.request("GET", f"/api/chats/{uid}")[0], 400)

    def test_a_failed_answer_can_be_tried_again_and_one_can_be_stopped(self):
        def broken(*args, **kwargs):
            raise history.AIUnavailable()
        server.agent.reply = broken
        uid = self.request("POST", "/api/chats", {})[1]["id"]
        self.request("POST", f"/api/chats/{uid}/messages", {"text": "Hello"})
        self.assertIn("isn't answering", self.wait_for_answer(uid)["error"])
        self.assertEqual([m["role"] for m in self.request("GET", f"/api/chats/{uid}")[1]["messages"]], ["user"])  # the message is kept

        def slow(model, thinking, messages, on_text, should_stop=lambda: False, **sizes):
            on_text("Part of an answer")
            while not should_stop():
                time.sleep(0.02)
            return {"text": "Part of an answer", "tools": [], "stopped": True}
        server.agent.reply = slow
        self.assertEqual(self.request("POST", f"/api/chats/{uid}/retry", {})[0], 200)
        self.assertEqual(self.request("POST", f"/api/chats/{uid}/messages", {"text": "Another"})[0], 400)  # still answering
        self.request("POST", f"/api/chats/{uid}/stop", {})
        self.assertTrue(self.wait_for_answer(uid)["stopped"])
        last = self.request("GET", f"/api/chats/{uid}")[1]["messages"][-1]
        self.assertEqual((last["content"], last["stopped"]), ("Part of an answer", True))  # what was written is kept
        self.request("POST", f"/api/chats/{uid}/delete", {})


class StartingTheAI(unittest.TestCase):
    """Ollama starts only when needed (here a stand-in that answers like it), and stops when MindChart started it."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(dir=support.DATA))
        port = free_port()
        fake = self.folder / "ollama"
        fake.write_text(f"""#!/bin/sh
[ "$1" = "serve" ] || exit 1
exec {sys.executable} -c 'import http.server
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{{\\"version\\": \\"test\\", \\"models\\": []}}")
http.server.HTTPServer(("127.0.0.1", {port}), H).serve_forever()'
""")
        fake.chmod(0o755)
        self.real = (ai.URL, ai.binary, ai.MANIFESTS)
        ai.URL, ai.binary = f"http://127.0.0.1:{port}", lambda: str(fake)

    def tearDown(self):
        ai.shut_down()
        ai.URL, ai.binary, ai.MANIFESTS = self.real

    def test_it_starts_when_needed_and_stops_with_mindchart(self):
        self.assertEqual(ai.status()["status"], "off")
        ai.ensure_running()
        self.assertEqual(ai.status()["status"], "on")
        self.assertTrue(ai.status()["started_by_mindchart"])
        ai.ensure_running()  # already on: nothing more is started
        ai.shut_down()
        self.assertEqual(ai.status()["status"], "off")

    def test_no_ollama_installed_says_so(self):
        ai.binary = lambda: None
        with self.assertRaisesRegex(ai.Unavailable, "isn't installed"):
            ai.ensure_running()

    def test_models_are_listed_without_starting_it(self):
        ai.MANIFESTS = self.folder / "manifests"
        manifest = ai.MANIFESTS / "library" / "bonsai-2" / "9b"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"layers": [{"mediaType": "application/vnd.ollama.image.model", "size": 3_000_000_000},
                                                   {"mediaType": "application/vnd.ollama.image.license", "size": 100}]}))
        self.assertEqual(ai.installed_on_disk(), [{"name": "bonsai-2:9b", "size": 3_000_000_000}])
        self.assertEqual([m["name"] for m in server.list_models()], ["bonsai-2:9b"])
        self.assertEqual(ai.status()["status"], "off")  # listing didn't start anything


if __name__ == "__main__":
    unittest.main()
