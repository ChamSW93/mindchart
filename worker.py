"""Background work, one job at a time while MindChart is unlocked: identifying uploads, then reading documents.

Uploads come first, since the clinician is waiting on their review cards. Confirmed documents are then read
(facts pulled out, fields filled in) so drafting a history later only has to write. The work stops while
MindChart is locked and carries on after unlocking, since what's waiting is kept in the encrypted store.
Background work doesn't count as activity, so MindChart still locks itself when left alone. Building a
history pauses it, so the two never use the AI at once.
"""
import contextlib
import logging
import threading
import time

import history
import intake
import vault

log = logging.getLogger("mindchart")
STATUS = {"current": None, "note": ""}  # what's being worked on, for the page
_busy = threading.Lock()  # held while a job runs
_pauses = 0
_pauses_lock = threading.Lock()


def run_forever(choose_model):
    """choose_model() -> (model name, whether it thinks), the model picked in the app."""
    while True:
        did_something = False
        try:
            if vault.is_unlocked() and not _pauses:
                with _busy:
                    if not _pauses:
                        did_something = step(choose_model)
        except PermissionError:  # locked part-way through; the job is picked up again after unlocking
            STATUS.update(current=None, note="")
            time.sleep(2)
        except history.AIUnavailable:
            STATUS.update(current=None, note="Waiting for the AI (Ollama) to start")
            time.sleep(20)
        except Exception:
            log.exception("Background work failed")
            STATUS["current"] = None
            time.sleep(10)
        if not did_something:
            time.sleep(2)


def step(choose_model):
    """Do the next job, if there is one. Returns whether there was."""
    upload = vault.next_upload_to_identify()
    if upload:
        model, thinking = choose_model()
        STATUS.update(current={"kind": "identify", "name": upload["name"], "progress": "Identifying"}, note="")
        try:
            suggestion = intake.identify(upload, model, thinking)
        except history.AIUnavailable:
            raise
        except Exception as error:  # the card still works with MindChart's own guess
            log.warning("Couldn't identify an upload: %s", type(error).__name__)
            suggestion, _ = intake.suggest(upload["name"], upload["pages"], upload["patient_uid"], pending=False)
            suggestion["ai_error"] = "The AI couldn't read this one; check the type and patient"
        vault.save_suggestion(upload["uid"], suggestion)
        STATUS["current"] = None
        return True

    doc = vault.next_document_to_read()
    if doc:
        read(doc, *choose_model())
        STATUS["current"] = None
        return True
    return False


def read(doc, model, thinking):
    """Read one confirmed document, or just fill in its fields if only its type changed."""
    STATUS.update(current={"kind": "read", "name": doc["name"], "progress": "Starting"}, note="")
    pull = {"facts": False, "fields": None} if doc["fields_only"] else doc["pull"]
    fields = vault.document_field_definitions()
    try:
        for update in history.read_document(doc, doc["name"], model, thinking, fields, pull):
            if "progress" in update:  # "Reading eval.pdf, part 2 of 3" -> "Part 2 of 3", as the sidebar already names the file
                text = update["progress"].replace(f" for {doc['name']}", "").replace(f" {doc['name']}", "")
                STATUS["current"]["progress"] = text.replace("Reading, part", "Part")
            elif "document_read" in update:
                read_ = update["document_read"]
                vault.save_document_facts(doc["id"], read_["info"], read_["facts"], model, read_["filled"])
    except (PermissionError, history.AIUnavailable):
        raise
    except Exception as error:
        log.warning("Couldn't read a document in the background: %s", type(error).__name__)
        vault.mark_unreadable(doc["id"])


@contextlib.contextmanager
def paused():
    """Hold background work (waiting for the current job to finish), e.g. while a history is built."""
    global _pauses
    with _pauses_lock:
        _pauses += 1
    try:
        with _busy:
            pass
        yield
    finally:
        with _pauses_lock:
            _pauses -= 1


def status():
    return {**STATUS, **vault.work_counts()}
