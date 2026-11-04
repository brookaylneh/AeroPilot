"""Keep multimodal JSON serialization out of the flight controller's interpreter."""

from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import httpx

from mission_planner.agents.models import Glimmer, Wonder
from mission_planner.agents.review import review_candidate


class WonderRequestError(RuntimeError):
    def __init__(self, kind, status_code, detail):
        super().__init__(kind, status_code, detail)
        self.kind, self.status_code, self.detail = kind, status_code, detail


def model_pool(workers):
    assert workers in (1, 4)
    return ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn"))


def wonder_response(args, kwargs):
    model = Wonder()
    try:
        return model.respond(*args, **kwargs)
    except httpx.HTTPStatusError as error:
        raise WonderRequestError("http", error.response.status_code, error.response.text) from error
    except httpx.TimeoutException as error:
        raise WonderRequestError("timeout", None, str(error)) from error
    except Exception as error:
        raise RuntimeError(f"Wonder request failed: {error}") from error
    finally:
        model.http.close()


def review_photo(look_for, prompt, metadata, photograph, detection, prefix):
    model = Glimmer()
    model.http.timeout = httpx.Timeout(60)
    try:
        return review_candidate(
            model, look_for, prompt, metadata, Path(photograph).read_bytes(), detection, prefix
        )
    except Exception as error:
        raise RuntimeError(f"Glimmer review failed: {type(error).__name__}: {error}") from error
    finally:
        model.http.close()


class WonderProcess:
    def __init__(self):
        self.pool = model_pool(1)

    def respond(self, *args, **kwargs):
        return self.pool.submit(wonder_response, args, kwargs).result()

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
