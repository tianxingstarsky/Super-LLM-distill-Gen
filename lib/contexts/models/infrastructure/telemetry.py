"""Optional Langfuse observations; telemetry failures never replay a paid request."""
from contextlib import contextmanager
from functools import lru_cache
import logging

LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def observation_client(public_key, secret_key, host):
    from langfuse import Langfuse
    return Langfuse(public_key=public_key, secret_key=secret_key, base_url=host)


def configured_observation_client(config):
    if not config:
        return None
    return observation_client(config["public_key"], config["secret_key"], config["host"])


def observation_update(observation, **fields):
    if observation is not None:
        try:
            observation.update(**fields)
        except Exception as error:
            LOGGER.warning("Langfuse observation update failed (%s)", type(error).__name__)


@contextmanager
def observe_request(client):
    manager = observation = None
    try:
        tracer = configured_observation_client(getattr(client, "observation_config", None))
        if tracer is not None:
            manager = tracer.start_as_current_observation(as_type="generation", name="model-request",
                model=client.model, metadata={"api_format": client.api_format})
            observation = manager.__enter__()
    except Exception as error:
        manager = None
        LOGGER.warning("Langfuse observation start failed (%s)", type(error).__name__)
    try:
        yield observation
    except BaseException as error:
        observation_update(observation, level="ERROR", status_message=type(error).__name__)
        raise
    finally:
        if manager is not None:
            try:
                manager.__exit__(None, None, None)
            except Exception as error:
                LOGGER.warning("Langfuse observation close failed (%s)", type(error).__name__)
