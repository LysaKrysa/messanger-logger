"""Shared defaults: one process keeps bot polling and pacing singular."""
workers = 1
worker_class = "gthread"
threads = 8
timeout = 120
graceful_timeout = 45
limit_request_line = 4094
limit_request_fields = 40
accesslog = None
errorlog = "-"
forwarded_allow_ips = ""
control_socket_disable = True


def post_worker_init(worker):
    pairing = getattr(worker.wsgi, "extensions", {}).get("pairing")
    if pairing is not None:
        import threading
        threading.Thread(target=pairing.run, name="telegram-pairing", daemon=True).start()


def worker_exit(server, worker):
    cleanup = getattr(getattr(worker, "wsgi", None), "extensions", {}).get("browser_cleanup")
    if cleanup is not None:
        cleanup()
    pairing = getattr(getattr(worker, "wsgi", None), "extensions", {}).get("pairing")
    if pairing is not None:
        pairing.stop.set()
