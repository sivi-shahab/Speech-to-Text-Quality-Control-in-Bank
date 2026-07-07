#!/usr/bin/env python3
"""
services/http_retry.py

_post_with_retry() dipindah verbatim dari speech_routes.py.
Dipisah karena dipakai oleh banyak service (webhook, backend forwarder,
upload ke API 8010) — sebelumnya semua saling coupled lewat 1 file besar.
"""

import time
import logging
import requests

logger = logging.getLogger(__name__)


def post_with_retry(
    url:         str,
    payload:     dict = None,
    files:       dict = None,
    data:        dict = None,
    headers:     dict = None,
    max_retries: int  = 3,
    timeout:     int  = 30,
) -> requests.Response:
    last_exc = None
    for attempt in range(max_retries):
        try:
            if files is not None:
                resp = requests.post(
                    url, files=files, data=data,
                    headers=headers, timeout=timeout,
                )
            else:
                h = {"Content-Type": "application/json"}
                if headers:
                    h.update(headers)
                resp = requests.post(url, json=payload, headers=h, timeout=timeout)
            if resp.status_code < 500:
                return resp
            logger.warning(
                "API %s HTTP %d (attempt %d/%d)",
                url, resp.status_code, attempt + 1, max_retries,
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            logger.warning(
                "API %s error attempt %d/%d: %s",
                url, attempt + 1, max_retries, e,
            )

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)

    raise RuntimeError(
        "API %s tidak merespons setelah %d percobaan. Last: %s"
        % (url, max_retries, last_exc)
    )
