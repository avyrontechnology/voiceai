"""Redis pool must survive transient connection loss (no 500 cascade).

Regression: the shared pool was built with bare defaults (no health checks,
no keepalive, no timeouts, no retries), so one Redis restart / Docker-network
blip poisoned pooled connections and EVERY authenticated endpoint 500'd with
redis.exceptions.ConnectionError until manual restart.
"""

from redis.exceptions import ConnectionError as RedisConnectionError

from local_setup.quickstart_server import build_redis_pool


def test_pool_probes_stale_connections():
    pool = build_redis_pool("redis://localhost:6379/0")
    try:
        assert pool.connection_kwargs.get("health_check_interval") == 30
    finally:
        pool.disconnect()


def test_pool_uses_keepalive_and_timeouts():
    pool = build_redis_pool("redis://localhost:6379/0")
    try:
        kwargs = pool.connection_kwargs
        assert kwargs.get("socket_keepalive") is True
        assert kwargs.get("socket_connect_timeout") == 5
        assert kwargs.get("socket_timeout") == 10
    finally:
        pool.disconnect()


def test_pool_retries_connection_errors_with_backoff():
    pool = build_redis_pool("redis://localhost:6379/0")
    try:
        conn = pool.make_connection()
        try:
            assert RedisConnectionError in conn.retry_on_error
            assert conn.retry._retries == 3
        finally:
            pass
    finally:
        pool.disconnect()
