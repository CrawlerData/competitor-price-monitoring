"""Minimal CrawlerData API client: live requests, async jobs with polling, retries.

Docs: https://app.crawlerdata.com/docs
Needs: requests
"""
import os
import random
import time

import requests

API_URL = "https://api.crawlerdata.com/v1"

HINTS = {
    401: "Invalid or missing API key. Check CRAWLERDATA_API_KEY.",
    402: "Out of credits. Top up at https://app.crawlerdata.com.",
}


class CrawlerDataError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class CrawlerData:
    """Small wrapper around POST /v1/jobs and GET /v1/jobs/{id}.

    credits_used adds up job.credits for every completed job, so scripts can
    report what a run cost.
    """

    def __init__(self, api_key=None, timeout=200, max_retries=4, failed_job_retries=2):
        api_key = api_key or os.environ.get("CRAWLERDATA_API_KEY")
        if not api_key:
            raise CrawlerDataError(
                "Set the CRAWLERDATA_API_KEY environment variable. "
                "Get a key (free trial credits for company emails) at https://app.crawlerdata.com/register"
            )
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {api_key}"
        self.timeout = timeout
        self.max_retries = max_retries
        self.failed_job_retries = failed_job_retries
        self.credits_used = 0
        self.last_job = None

    def _request(self, method, path, **kwargs):
        res = None
        for attempt in range(self.max_retries + 1):
            try:
                res = self.session.request(method, API_URL + path, timeout=self.timeout, **kwargs)
            except (requests.ConnectionError, requests.Timeout):
                if attempt == self.max_retries:
                    raise
                time.sleep(2 ** attempt + random.random())
                continue
            # 429 = rate limited (100/min, 20/s per key), 5xx = temporary. Wait and retry.
            if res.status_code == 429 or res.status_code >= 500:
                if attempt == self.max_retries:
                    break
                reset = res.headers.get("X-RateLimit-Reset")
                wait = max(1, int(reset) - int(time.time())) if reset and reset.isdigit() else 2 ** attempt
                time.sleep(min(wait, 60) + random.random())
                continue
            break
        if res.status_code >= 400:
            # 400 bad params, 401 bad key, 402 out of credits: retrying will not help.
            hint = HINTS.get(res.status_code, "")
            raise CrawlerDataError(f"HTTP {res.status_code}: {res.text[:300]} {hint}".strip(), res.status_code)
        return res.json()

    def _track(self, job):
        self.last_job = job
        self.credits_used += job.get("credits") or 0

    def run(self, endpoint, params, queue="live", postback_url=None):
        """Submit a job and return the raw response ({job} or {job, result})."""
        body = {"endpoint": endpoint, "params": params, "queue": queue}
        if postback_url:
            body["postbackUrl"] = postback_url
        return self._request("POST", "/jobs", json=body)

    def live_response(self, endpoint, params):
        """Run a live request and return the full {job, result} response.

        Failed jobs cost 0 credits, so they are retried a couple of times.
        """
        for attempt in range(self.failed_job_retries + 1):
            data = self.run(endpoint, params, queue="live")
            job = data["job"]
            if job["status"] == "completed":
                self._track(job)
                return data
            if attempt < self.failed_job_retries:
                time.sleep(5 * (attempt + 1))
        raise CrawlerDataError(f"job {job['id']} failed: {job.get('error')}")

    def live(self, endpoint, params):
        """Run a live request and return only the result."""
        return self.live_response(endpoint, params)["result"]

    def submit(self, endpoint, params, queue="fast", postback_url=None):
        """Queue a job on the fast or regular queue and return its id."""
        return self.run(endpoint, params, queue=queue, postback_url=postback_url)["job"]["id"]

    def wait(self, job_id, interval=5, max_wait=600):
        """Poll a job until it completes (returns the result) or fails (raises)."""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            data = self._request("GET", f"/jobs/{job_id}")
            status = data["job"]["status"]
            if status == "completed":
                self._track(data["job"])
                return data["result"]
            if status == "failed":
                raise CrawlerDataError(f"job {job_id} failed: {data['job'].get('error')}")
            time.sleep(interval)
        raise TimeoutError(f"job {job_id} still running after {max_wait}s")
