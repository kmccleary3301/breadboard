"""pi requests responsibility owner."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from .pi_target import load_target_document
from .request_bytes import guarded_changes, project_guarded_values, raw_mismatch, request_json_problem
from .pi_environment_guards import project_pi_environment

def is_summary_request(
    request_body: Mapping[str, Any],
    profile_id: str = "breadboard.pi-coding-agent.v0.73.1",
) -> bool:
    """Check if request body is a compaction summary request by checking profile system prompt equality."""
    from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES

    profile = NATIVE_STREAM_PROFILES.get(profile_id)
    if not profile or not profile.compaction_summary_system_prompt:
        return False
    msgs = request_body.get("messages", [])
    if not msgs or not isinstance(msgs, list):
        return False
    first = msgs[0]
    if isinstance(first, dict) and first.get("role") == "system":
        return first.get("content") == profile.compaction_summary_system_prompt
    return False

def normalize_request_body(
    body: Dict[str, Any],
    allowed_deviations: Sequence[str],
    *,
    is_stock: bool = False,
    target_id: str = "pi-r3@0.73.1",
    episode_tools: Any = None,
    episode_max_tokens: Optional[int] = None,
    reserve_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Normalize a request body removing strictly the recorded deviation fields.

    Admitted deviations:
    - compaction_summary_episode_tools: On summary requests, remove `tools` field.
    - compaction_summary_max_tokens: On summary requests, remove `max_tokens` / `max_completion_tokens`.

    Declared transform (native-config advertisement):
    - When is_stock is True:
      * advertisement.tools.read: verify native_sha256, replace with declared description.
      * advertisement.prompt.remove_exact: require one exact occurrence, remove it.
    No date, directory, package-path or representation normalization is admitted.
    """
    normalized = deepcopy(body)
    is_sum = is_summary_request(normalized)
    if is_sum:
        if "compaction_summary_episode_tools" in allowed_deviations:
            if is_stock:
                if "tools" in normalized:
                    raise ValueError("Stock Pi summary unexpectedly carries tools")
            elif episode_tools is None or json.dumps(normalized.get("tools"), sort_keys=True) != json.dumps(episode_tools, sort_keys=True):
                raise ValueError("BB summary tools differ from episode tools")
            normalized.pop("tools", None)
        if "compaction_summary_max_tokens" in allowed_deviations:
            if is_stock:
                reserve = reserve_tokens if reserve_tokens is not None else load_target_document(
                    target_id, "native-config.json"
                )["agent"]["compaction_settings"]["reserveTokens"]
                content = normalized["messages"][-1]["content"]
                text = content if isinstance(content, str) else content[-1]["text"]
                # Stock history/prefix budgets: 0.73.1 compaction.js:433,593;
                # 0.57.1 compaction.js:424,590.
                ratio = 0.5 if "\n\nThis is the PREFIX of a turn" in text else 0.8
                expected_cap = int(ratio * reserve)
            else:
                if episode_max_tokens is None:
                    raise ValueError("Episode cap is required for summary validation")
                expected_cap = episode_max_tokens
            if normalized.get("max_tokens") != expected_cap or "max_completion_tokens" in normalized:
                raise ValueError("Summary token cap differs from its admitted budget")
            normalized.pop("max_tokens", None)

    adv = load_target_document(target_id, "native-config.json")["advertisement"]
    if not isinstance(adv, dict):
        raise ValueError(f"Invalid native advertisement for {target_id}")

    read_adv = adv.get("tools", {}).get("read", {})
    remove_exact = adv.get("prompt", {}).get("remove_exact", [])

    if is_stock and read_adv:
        for t in normalized.get("tools", []):
            fn = t.get("function", {})
            if fn.get("name") == "read":
                desc = fn.get("description", "")
                actual_sha = "sha256:" + hashlib.sha256(desc.encode("utf-8")).hexdigest()
                if actual_sha != read_adv.get("native_sha256"):
                    raise ValueError("Stock read description differs from declared native hash")
                fn["description"] = read_adv["description"]
    if not is_stock and read_adv:
        for tool in normalized.get("tools", []):
            fn = tool.get("function", {})
            if fn.get("name") == "read" and fn.get("description") != read_adv["description"]:
                raise ValueError("BB read description differs from declared advertisement")

    for m in normalized.get("messages", []):
        if m.get("role") == "system":
            content = m.get("content", "")
            if is_stock and not is_sum and remove_exact:
                for needle in remove_exact:
                    if content.count(needle) != 1:
                        raise ValueError("Declared prompt needle must occur exactly once")
                    content = content.replace(needle, "")

            m["content"] = content

    return normalized

def compare_recorded_requests(
    stock_requests: List[Dict[str, Any]],
    bb_requests: List[Dict[str, Any]],
    allowed_deviations: Sequence[str],
    *,
    target_id: str = "pi-r3@0.73.1",
    episode_tools: Any = None,
    episode_max_tokens: Optional[int] = None,
    reserve_tokens: Optional[int] = None,
    deviation_records: Optional[List[Dict[str, Any]]] = None,
    stock_environment: Optional[Dict[str, Any]] = None,
    bb_environment: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[Dict[str, Any]]]:
    """Byte-compare stock and BreadBoard requests after applying deviation normalizer."""
    diffs: List[Dict[str, Any]] = []
    main = next((row["json"] for row in bb_requests if not is_summary_request(row["json"])), None)
    if main is not None:
        episode_tools = main["tools"]
        episode_max_tokens = main["max_tokens"]

    if len(stock_requests) != len(bb_requests):
        diffs.append({
            "type": "count_mismatch",
            "stock_count": len(stock_requests),
            "bb_count": len(bb_requests),
        })

    max_idx = max(len(stock_requests), len(bb_requests))
    for i in range(max_idx):
        if i >= len(stock_requests):
            diffs.append({"index": i, "type": "extra_breadboard_request", "bb_request": bb_requests[i]})
            continue
        if i >= len(bb_requests):
            diffs.append({"index": i, "type": "missing_breadboard_request", "stock_request": stock_requests[i]})
            continue
        problem = request_json_problem(stock_requests[i], bb_requests[i], i)
        if problem is not None:
            diffs.append(problem)
            continue
        raw_stock = bytes.fromhex(stock_requests[i]["raw_body_bytes"])
        raw_bb = bytes.fromhex(bb_requests[i]["raw_body_bytes"])
        mismatch = raw_mismatch(raw_stock, raw_bb, i)
        if mismatch is None and stock_requests[i].get("path") == bb_requests[i].get("path") and stock_requests[i].get("method") == bb_requests[i].get("method"):
            continue

        environment_records: Dict[str, List[Dict[str, Any]]] = {"stock": [], "bb": []}
        stock_guarded, bb_guarded = raw_stock, raw_bb
        try:
            if stock_environment is not None or bb_environment is not None:
                if stock_environment is None or bb_environment is None:
                    raise ValueError("Pi source environment observations are required for both sides")
                stock_guarded, environment_records["stock"] = project_pi_environment(
                    raw_stock, stock_environment, index=i, side="stock",
                )
                bb_guarded, environment_records["bb"] = project_pi_environment(
                    raw_bb, bb_environment, index=i, side="bb",
                )
            stock_body, bb_body = json.loads(stock_guarded), json.loads(bb_guarded)
            stock_norm = normalize_request_body(
                stock_body, allowed_deviations, is_stock=True,
                target_id=target_id, reserve_tokens=reserve_tokens,
            )
            bb_norm = normalize_request_body(
                bb_body, allowed_deviations, is_stock=False,
                target_id=target_id, episode_tools=episode_tools,
                episode_max_tokens=episode_max_tokens,
            )
        except ValueError as exc:
            diffs.append({"index": i, "type": "deviation_value_diff", "message": str(exc),
                          "raw_difference": mismatch})
            continue

        stock_bytes = project_guarded_values(stock_guarded, stock_norm)
        bb_bytes = project_guarded_values(bb_guarded, bb_norm)
        route_matches = (stock_requests[i].get("path") == bb_requests[i].get("path")
                         and stock_requests[i].get("method") == bb_requests[i].get("method"))
        if mismatch is not None and deviation_records is not None:
            changes = {"stock": guarded_changes(stock_body, stock_norm),
                       "bb": guarded_changes(bb_body, bb_norm)}
            names = [name for name in allowed_deviations if name in {"compaction_summary_episode_tools", "compaction_summary_max_tokens"} and is_summary_request(stock_requests[i]["json"])]
            if any(change["path"].endswith("/description") for change in changes["stock"]):
                names.append("pi_read_description_advertisement")
            if any(change["path"].endswith("/content") for change in changes["stock"]):
                names.append("pi_prompt_remove_exact_advertisement")
            names.extend(dict.fromkeys(record["name"] for records in environment_records.values() for record in records))
            deviation_records.append({**mismatch, "names": names, "changes": changes,
                                      "environment": environment_records,
                                      "authority": target_id + "/harness.yaml#policy.deviations and native-config.json#advertisement; pinned Pi source footer/environment observations",
                                      "accepted": stock_bytes == bb_bytes and route_matches})

        if not route_matches:
            diffs.append({"index": i, "type": "request_route",
                          "stock_route": [stock_requests[i].get("method"), stock_requests[i].get("path")],
                          "bb_route": [bb_requests[i].get("method"), bb_requests[i].get("path")]})
        if stock_bytes != bb_bytes:
            diffs.append({
                "index": i,
                "type": "body_diff",
                "stock_normalized": stock_norm,
                "bb_normalized": bb_norm,
            })

    return len(diffs) == 0, diffs
