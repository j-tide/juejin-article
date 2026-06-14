from pathlib import Path
import tempfile
from .delivery import DeliveryClient, DeliveryService
from .runtime import digest


def experiment():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        naive = DeliveryService(root / "naive.db")
        try:
            naive.submit("new-key-1", b"report", lose_reply=True)
        except TimeoutError:
            pass
        naive.submit("new-key-2", b"report")
        service = DeliveryService(root / "service.db")
        client = DeliveryClient(root / "intent.db", service)
        key = "task-A/review/" + digest(b"report")
        assert client.send(key, b"report", lose_reply=True) is None
        unknown = client.state(key)
        client.db.close()
        client = DeliveryClient(root / "intent.db", service)
        receipt = client.reconcile(key)
        client.send(key, b"report")
        try:
            service.submit(key, b"modified report")
            raise AssertionError("conflicting payload accepted")
        except ValueError as error:
            conflict = str(error)
        result = {"episode": 3, "naive_delivery_count": naive.count(),
                  "stable_key_delivery_count": service.count(),
                  "after_timeout": unknown, "after_reconcile": client.state(key),
                  "receipt": receipt, "payload_conflict": conflict}
        assert result["naive_delivery_count"] == 2
        assert result["stable_key_delivery_count"] == 1
        assert unknown == "UNKNOWN" and client.state(key) == "DELIVERED"
        for db in (naive.db, service.db, client.db):
            db.close()
        return result
