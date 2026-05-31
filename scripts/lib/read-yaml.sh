#!/usr/bin/env bash
# scripts/lib/read-yaml.sh
#
# Sourceable helper that extracts a scalar value from a YAML file by dot-path key.
#
# Usage (after sourcing):
#   read_yaml ".agents/governance/verify-gates.yaml" "gates.static.ruff_violations"
#
# Returns the value on stdout; exits non-zero if key not found.
#
# Strategy:
#   1. Use `yq` (https://github.com/mikefarah/yq) if available — handles full YAML.
#   2. Fall back to a grep/sed heuristic for simple scalar values (no nested mapping
#      as value, no multi-line strings).  Adequate for verify-gates.yaml which only
#      stores scalars (integers, booleans, strings) at the leaf keys used by gates.
#
# Fallback limitations (documented):
#   - Does NOT handle YAML anchors, multi-line values, or in-line flow maps.
#   - Key must be globally unique enough that the last path component is unambiguous
#     within the YAML; duplicated leaf key names across sibling sections may return
#     the first match.
#
# §6.3 discipline: thresholds always live in verify-gates.yaml; never hardcoded.

GATES_YAML="${GATES_YAML:-.agents/governance/verify-gates.yaml}"

read_yaml() {
    local yaml_file="$1"
    local key_path="$2"   # dot-separated, e.g. gates.static.ruff_violations

    if [[ -z "$yaml_file" || -z "$key_path" ]]; then
        echo "read_yaml: usage: read_yaml <file> <dot.path.key>" >&2
        return 1
    fi

    if [[ ! -f "$yaml_file" ]]; then
        echo "read_yaml: file not found: $yaml_file" >&2
        return 1
    fi

    # ── Strategy 1: yq ─────────────────────────────────────────────────────────
    if command -v yq &>/dev/null; then
        local yq_path=".${key_path}"
        local value
        value=$(yq e "${yq_path}" "$yaml_file" 2>/dev/null)
        if [[ $? -eq 0 && "$value" != "null" && -n "$value" ]]; then
            echo "$value"
            return 0
        fi
    fi

    # ── Strategy 2: grep/sed heuristic ────────────────────────────────────────
    # Extract the last component of the dot-path as the leaf key.
    local leaf_key="${key_path##*.}"

    # Match lines of the form: "  leaf_key: value" (any leading whitespace, optional
    # trailing comment).  Strip leading/trailing whitespace from the value.
    local raw
    raw=$(grep -E "^[[:space:]]*${leaf_key}[[:space:]]*:" "$yaml_file" \
          | head -1 \
          | sed -E "s/^[[:space:]]*${leaf_key}[[:space:]]*:[[:space:]]*//" \
          | sed -E 's/[[:space:]]*#.*$//' \
          | sed -E 's/^[[:space:]]+|[[:space:]]+$//')

    if [[ -n "$raw" ]]; then
        echo "$raw"
        return 0
    fi

    echo "read_yaml: key '${key_path}' not found in ${yaml_file}" >&2
    return 1
}

# Convenience: read a threshold from the canonical gates file.
# Usage: gate_threshold "static.ruff_violations"
gate_threshold() {
    local key_path="$1"
    read_yaml "$GATES_YAML" "gates.${key_path}"
}
