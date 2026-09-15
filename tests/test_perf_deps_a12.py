"""A12 PERF-DEPS fixer — TDD perf tests (red before fix, green after).

Owned: PBKDF2 off-loop, Redis index sets (no KEYS/SCAN on hot paths),
gRPC join + file I/O offload, TaskRegistry spread, dep pins, docker files.
"""

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class _FakeRedisA12:
    """Async Redis double with hash + set support and call counters."""

    def __init__(self) -> None:
        self.kv: dict = {}
        self.sets: dict = {}
        self.hashes: dict = {}
        self.keys_calls = 0
        self.scan_calls = 0
        self.get_calls = 0
        self.mget_calls = 0
        self.smembers_calls = 0

    async def set(self, key: str, value: str, **_: object) -> None:
        self.kv[str(key)] = str(value)

    async def get(self, key: str):
        self.get_calls += 1
        return self.kv.get(str(key))

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            ks = str(k)
            if ks in self.kv:
                del self.kv[ks]
                n += 1
            for h in list(self.hashes.values()):
                h.pop(ks, None)
            for s in self.sets.values():
                s.discard(ks)
        # also drop whole-set/hash keys themselves
        for k in keys:
            ks = str(k)
            self.sets.pop(ks, None)
            self.hashes.pop(ks, None)
        return n

    async def exists(self, key: str) -> int:
        return 1 if str(key) in self.kv else 0

    async def mget(self, keys):
        self.mget_calls += 1
        return [self.kv.get(str(k)) for k in keys]

    async def sadd(self, key: str, *members: str) -> int:
        self.sets.setdefault(str(key), set()).update(str(m) for m in members)
        return len(members)

    async def srem(self, key: str, *members: str) -> int:
        s = self.sets.get(str(key), set())
        n = 0
        for m in members:
            if str(m) in s:
                s.discard(str(m))
                n += 1
        return n

    async def smembers(self, key: str):
        self.smembers_calls += 1
        return set(self.sets.get(str(key), set()))

    async def scard(self, key: str) -> int:
        return len(self.sets.get(str(key), set()))

    async def hset(self, key: str, mapping=None, **kwargs) -> int:
        h = self.hashes.setdefault(str(key), {})
        data = dict(mapping or {})
        data.update(kwargs)
        for f, v in data.items():
            h[str(f)] = str(v)
        return len(data)

    async def hget(self, key: str, field: str):
        h = self.hashes.get(str(key), {})
        return h.get(str(field))

    async def hdel(self, key: str, *fields: str) -> int:
        h = self.hashes.get(str(key), {})
        n = 0
        for f in fields:
            if str(f) in h:
                del h[str(f)]
                n += 1
        return n

    async def keys(self, pattern: str):
        self.keys_calls += 1
        import fnmatch

        return [k for k in self.kv if fnmatch.fnmatch(k, pattern)]

    def scan_iter(self, match):
        self.scan_calls += 1
        import fnmatch

        matched = [k for k in self.kv if fnmatch.fnmatch(k, match)]

        class _Iter:
            def __init__(self, items):
                self._items = items

            def __aiter__(self):
                async def _gen():
                    for item in self._items:
                        yield item

                return _gen()

        return _Iter(matched)

    async def lpush(self, key: str, value: str) -> None:
        self.kv.setdefault(str(key) + ":list", []).append(str(value))

    async def lrange(self, key: str, start: int, stop: int):
        return (self.kv.get(str(key) + ":list", []) or [])[start : stop + 1]

    async def llen(self, key: str) -> int:
        return len(self.kv.get(str(key) + ":list", []) or [])

    async def lpush_ledger(self, *a, **k):
        pass


async def _make_user_store():
    from voiceai.platform.models import User
    from voiceai.platform.store import RedisStore

    fake = _FakeRedisA12()
    store = RedisStore(fake)  # type: ignore[arg-type]
    user = User(user_id="usr-1", email="Owner@Acme.test", name="O", password_hash="x", role="owner")
    await store.save_user(user)
    return store, fake, user


async def test_pbkdf2_hash_offloaded_via_to_thread(monkeypatch):
    """hash/verify must run in a worker thread (600k iterations blocks the audio loop)."""
    import asyncio

    from voiceai.platform import auth as auth_mod

    assert hasattr(auth_mod, "ahash_password"), "missing async ahash_password wrapper"
    assert hasattr(auth_mod, "averify_password"), "missing async averify_password wrapper"
    calls: list = []
    orig = asyncio.to_thread

    async def _spy(func, /, *args, **kwargs):
        calls.append((func, args))
        return await orig(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _spy)
    hashed = await auth_mod.ahash_password("correct-horse-1")
    assert hashed.startswith("pbkdf2_sha256$")
    assert calls, "ahash_password must await asyncio.to_thread"
    calls.clear()
    assert await auth_mod.averify_password("correct-horse-1", hashed) is True
    assert calls, "averify_password must await asyncio.to_thread"
    assert await auth_mod.averify_password("wrong", hashed) is False


async def test_auth_router_uses_async_hash():
    """auth_router hot paths (signup/login/accept/password) must await the offloaded helpers."""
    src = (ROOT / "voiceai" / "platform" / "auth_router.py").read_text()
    for token in ("ahash_password", "averify_password"):
        assert token in src, f"auth_router must use {token} (PBKDF2 off event loop)"


async def test_redis_user_hot_paths_use_indexes_not_scan():
    """get_user_by_email/count/list must hit index sets + by-email hash (no SCAN/KEYS)."""
    store, fake, user = await _make_user_store()
    fake.keys_calls = 0
    fake.scan_calls = 0
    fake.get_calls = 0
    fake.mget_calls = 0
    got = await store.get_user_by_email("owner@acme.test")
    assert got is not None and got.user_id == "usr-1"
    assert fake.keys_calls == 0, "get_user_by_email must not use KEYS"
    assert fake.scan_calls == 0, "get_user_by_email must use by-email hash, not SCAN"
    fake.keys_calls = 0
    fake.scan_calls = 0
    n = await store.count_users()
    assert n == 1
    assert fake.keys_calls == 0 and fake.scan_calls == 0, "count_users must use SCARD, not SCAN/KEYS"
    fake.keys_calls = 0
    fake.scan_calls = 0
    users = await store.list_users()
    assert len(users) == 1
    assert fake.keys_calls == 0 and fake.scan_calls == 0, "list_users must use index SET+MGET"


async def test_redis_api_key_lookup_uses_hash_not_list_scan():
    """API-key auth (per-request) must resolve via by-hash index, not list+SCAN."""
    from voiceai.platform import auth as auth_mod
    from voiceai.platform.models import ApiKey
    from voiceai.platform.store import RedisStore

    fake = _FakeRedisA12()
    store = RedisStore(fake)  # type: ignore[arg-type]
    from voiceai.platform.auth import token_hash

    secret = "sk-test-secret-123"
    key = ApiKey(
        key_id="key-1", name="k", prefix=secret[:6], key_hash=token_hash(secret), scopes=["*"], created_by=None
    )
    await store.save_api_key(key)
    assert hasattr(store, "get_api_key_by_hash"), "RedisStore needs get_api_key_by_hash index lookup"
    fake.keys_calls = 0
    fake.scan_calls = 0
    found = await store.get_api_key_by_hash(token_hash(secret))
    assert found is not None and found.key_id == "key-1"
    assert fake.keys_calls == 0 and fake.scan_calls == 0
    # End-to-end principal path must not list-scan.
    fake.keys_calls = 0
    fake.scan_calls = 0
    principal = await auth_mod._principal_from_api_key(store, secret)
    assert principal is not None
    assert fake.keys_calls == 0, "_principal_from_api_key must not KEYS-scan"


async def test_redis_invite_token_lookup_uses_index():
    from voiceai.platform.models import Invite
    from voiceai.platform.store import RedisStore
    from voiceai.platform.models import utcnow
    from datetime import timedelta

    fake = _FakeRedisA12()
    store = RedisStore(fake)  # type: ignore[arg-type]
    inv = Invite(
        invite_id="inv-1",
        email="m@acme.test",
        role="member",
        token_hash="tokhash-1",
        expires_at=utcnow() + timedelta(hours=1),
    )
    await store.save_invite(inv)
    assert hasattr(store, "get_invite_by_token_hash"), "need indexed invite lookup"
    fake.keys_calls = 0
    fake.scan_calls = 0
    got = await store.get_invite_by_token_hash("tokhash-1")
    assert got is not None and got.invite_id == "inv-1"
    assert fake.keys_calls == 0 and fake.scan_calls == 0


async def test_redis_session_user_index_avoids_scan():
    from voiceai.platform.models import SessionRecord, utcnow
    from datetime import timedelta

    _, fake_unused = None, None
    from voiceai.platform.store import RedisStore

    fake = _FakeRedisA12()
    store = RedisStore(fake)  # type: ignore[arg-type]
    for i in range(3):
        await store.save_session(
            SessionRecord(
                token_hash=f"tok-{i}",
                user_id="usr-1",
                org_id="default",
                kind="session",
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
    fake.keys_calls = 0
    fake.scan_calls = 0
    n = await store.delete_user_sessions("usr-1")
    assert n == 3
    assert fake.keys_calls == 0 and fake.scan_calls == 0, "delete_user_sessions must use by-user SET"


async def test_redis_collections_use_index_sets():
    """batches/kbs/tools must list via index SET+MGET (no SCAN/KEYS after save)."""
    from voiceai.platform.models import Batch, BatchStatus, KnowledgeBase, Tool
    from voiceai.platform.store import RedisStore

    fake = _FakeRedisA12()
    store = RedisStore(fake)  # type: ignore[arg-type]
    await store.save_batch(Batch(batch_id="b1", agent_id="a1", name="t", status=BatchStatus.DRAFT, entries=[]))
    await store.save_kb(KnowledgeBase(kb_id="kb1", name="k"))
    await store.save_tool(Tool(tool_id="t1", name="tool", kind="datetime", config={}))
    fake.keys_calls = 0
    fake.scan_calls = 0
    fake.mget_calls = 0
    assert len(await store.list_batches()) == 1
    assert len(await store.list_kbs()) == 1
    assert len(await store.list_tools()) == 1
    assert fake.keys_calls == 0, "collection lists must not use KEYS"
    assert fake.scan_calls == 0, "collection lists must use index sets, not SCAN"


async def test_quickstart_scan_helper_avoids_keys(monkeypatch):
    """Agent listing helper must prefer SCAN (non-blocking) over KEYS."""
    import local_setup.quickstart_server as qs

    assert hasattr(qs, "_scan_keys_nonblocking"), "quickstart needs _scan_keys_nonblocking helper"
    fake = _FakeRedisA12()
    fake.kv["agent-1"] = '{"a": 1}'
    out = await qs._scan_keys_nonblocking(fake, "*")  # type: ignore[arg-type]
    assert "agent-1" in out
    assert fake.keys_calls == 0, "SCAN path must not call KEYS"
    assert fake.scan_calls >= 1


async def test_google_toggle_join_offloaded(monkeypatch):
    """gRPC thread join (up to 2s) must not block the event loop."""
    import asyncio

    import voiceai.transcriber.google_transcriber as gmod

    src = pathlib.Path(gmod.__file__).read_text()
    assert "to_thread" in src, "google_transcriber must offload blocking join via to_thread"
    # Behavioural: toggle_connection with a live thread must await to_thread(join).
    calls: list = []

    async def _spy(func, /, *args, **kwargs):
        calls.append(getattr(func, "__name__", str(func)))
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _spy)
    tr = gmod.GoogleTranscriber.__new__(gmod.GoogleTranscriber)
    import queue

    tr._running = True
    tr.connection_on = True
    tr.connection_authenticated = True
    tr.transcription_task = None
    tr._audio_q = queue.Queue()
    import threading
    import time

    evt = threading.Event()

    def _block():
        evt.wait(timeout=5)

    th = threading.Thread(target=_block, daemon=True)
    th.start()
    tr._grpc_thread = th
    try:
        await asyncio.wait_for(tr.toggle_connection(), timeout=5)
    finally:
        evt.set()
        th.join(timeout=2)
    assert any("join" in c for c in calls), f"toggle_connection must to_thread(join), got {calls}"


async def test_file_io_offloaded(monkeypatch):
    """Sync open() on async paths (store_file/audio/prompts) must go via to_thread."""
    import asyncio

    import voiceai.helpers.utils as utils_mod

    src = pathlib.Path(utils_mod.__file__).read_text()
    assert src.count("asyncio.to_thread") >= 2, "helpers/utils async file I/O must use asyncio.to_thread"
    calls: list = []
    orig = asyncio.to_thread

    async def _spy(func, /, *args, **kwargs):
        calls.append(getattr(func, "__name__", str(func)))
        return await orig(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _spy)
    calls.clear()
    await utils_mod.store_file(file_key="a12/x.json", file_data={"a": 1}, content_type="json", local=True)
    assert calls, "store_file local write must go via to_thread"


def test_dep_pins_and_audioop_backport():
    req = (ROOT / "requirements.txt").read_text()
    assert "aiohttp==3.14.3" in req, "aiohttp must bump 3.14.1 -> 3.14.3 (3 advisories)"
    assert "aiohttp==3.14.1" not in req
    assert "fastapi==0.109.1" not in req, "fastapi 0.109.1 pins starlette 0.35.1 (9 advisories) — must bump"
    assert "audioop-lts" in req, "audioop removed in 3.13 (18 sites) — need audioop-lts backport"


def test_docker_and_compose_hygiene():
    di = (ROOT / ".dockerignore").read_text()
    for token in ("*.wav", "*.mp3"):
        assert token in di, f".dockerignore must exclude {token} (21MB wavs bloat image)"
    assert "ambient_noise" in di or "presets" in di
    compose = (ROOT / "docker-compose.yml").read_text()
    assert ".aws" in compose
    # The ~/.aws bind is host-optional: must be documented so missing dirs don't break compose up.
    assert "optional" in compose.lower() or "missing" in compose.lower() or "local" in compose.lower()
    render = (ROOT / "render.yaml").read_text()
    assert "free" in render.lower()
    assert "sleep" in render.lower() or "cold" in render.lower(), "render free plan must note sleep/cold-start"
    start = (ROOT / "start.sh").read_text()
    assert start.lstrip().startswith("#!"), "start.sh needs a shebang line"


def test_task_registry_spread():
    """Fire-and-forget tasks in owned scope must use safe_task/TaskRegistry (no bare create_task)."""
    for rel in (
        "voiceai/input_handlers/telephony.py",
        "voiceai/helpers/observable_variable.py",
        "voiceai/output_handlers/telephony_providers/sip_trunk.py",
    ):
        src = (ROOT / rel).read_text()
        assert "safe_task" in src or "TaskRegistry" in src, f"{rel} must use safe_task/TaskRegistry"


def test_except_pass_classified():
    """Owned helpers must not swallow silently (log_ignored/classify/summarize)."""
    for rel in (
        "voiceai/helpers/rag_service_client.py",
        "voiceai/s2s/openai_realtime_s2s.py",
    ):
        src = (ROOT / rel).read_text()
        assert "log_ignored" in src or "classify_exception" in src or "summarize_exception" in src, (
            f"{rel} must classify/log instead of bare pass"
        )
