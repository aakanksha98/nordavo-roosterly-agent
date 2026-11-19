#!/usr/bin/env python3
"""Score decide() against GET /examples. Gold labels come only from the API."""

import sys

from run_agent import decide, load_dotenv, make_client

FIELDS = ("language", "category", "priority", "action")


def rows_of(data):
    if isinstance(data, list):
        return data
    for key in ("examples", "tickets", "data"):
        if isinstance(data.get(key), list):
            return data[key]
    raise SystemExit(f"unexpected /examples shape: {list(data)[:8]}")


def main():
    load_dotenv()
    data = rows_of(make_client().request("GET", "/examples"))
    totals = {field: 0 for field in FIELDS}
    totals["all"] = 0
    for example in data:
        gold = example["label"]
        pred = decide(example.get("subject") or "", example.get("body") or "")
        bad = [field for field in FIELDS if pred[field] != gold[field]]
        for field in FIELDS:
            totals[field] += pred[field] == gold[field]
        totals["all"] += not bad
        if bad:
            shown = {field: [gold[field], pred[field]] for field in bad}
            print(f"{example.get('id')} {shown} pred_faq={pred.get('faq')}")
    n = len(data)
    print(" ".join(f"{name} {totals[name]}/{n}" for name in (*FIELDS, "all")))


if __name__ == "__main__":
    main()
