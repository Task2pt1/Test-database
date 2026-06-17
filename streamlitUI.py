# streamlitUI.py
# Neo4j -> global search + cached subtree fetch -> Streamlit UI

# =============================================================================
# SECTION 1 — SETUP
# =============================================================================
import json
from collections import defaultdict, deque
from typing import Any

import pandas as pd
import streamlit as st
from neo4j import Driver, GraphDatabase
import base64
import json
import requests

from collections import defaultdict, deque
from typing import Any

import pandas as pd
import streamlit as st
from neo4j import Driver, GraphDatabase



st.set_page_config(page_title="Material Ontology Explorer", layout="wide")

NODE_LABEL = "Material"
CHILD_REL = "HAS_CHILD"

ATTR_BLOCKS = (
    "engineering",
    "activity",
    "lcia",
    "material_cost",
    "standards",
    "synonyms",
    "citation",
    "comment",
    "notes",
    "region",
)

META_KEYS = {"name", "id", "code", "database", "vector", "placement"}

FILTER_ATTR_OPTIONS = ["(no filter)",  *ATTR_BLOCKS]
INDENT_PX_PER_LEVEL = 78
MAX_INDENT_LEVELS = 6
MAX_INDENT_PX = INDENT_PX_PER_LEVEL * MAX_INDENT_LEVELS
MAX_CHILD_SPACER = 0.24
CHILD_SPACER_PER_LEVEL = 0.04
PATH_SEP = "/"
SUBMIT_MATERIAL_ID = "user_participation"

# =============================================================================
# SECTION 2 — CSS
# =============================================================================
st.markdown(
    """
    <style>
    /* BLOCK A — App title */
    .app-title {
        font-size: 1.75rem;
        font-weight: 600;
        margin-bottom: 0.5rem;
    }

    /* BLOCK B — Compare tab table */
    .compare-scroll {
        overflow-x: auto;
        max-width: 100%;
        border: 1px solid rgba(250, 250, 250, 0.10);
        border-radius: 12px;
    }

    .compare-table {
        border-collapse: collapse;
        table-layout: fixed;
        width: max-content;
        min-width: 100%;
        font-size: 0.88rem;
    }

    .compare-table th,
    .compare-table td {
        border: 1px solid rgba(250, 250, 250, 0.08);
        padding: 8px 10px;
        text-align: left;
        vertical-align: top;
    }

    .compare-table th {
        position: sticky;
        top: 0;
        background: #1f2430;
    }

    .compare-table .sticky-attr {
        position: sticky;
        left: 0;
        background: #111827;
        min-width: 260px;
        font-weight: 600;
    }

    .compare-table .material-col {
        min-width: 180px;
    }

    /* BLOCK C — Sidebar */
    section[data-testid="stSidebar"] div[data-testid="column"] .stButton > button {
        padding: 0.1rem 0.35rem !important;
        min-height: 1.35rem !important;
        font-size: 0.8rem !important;
    }

    [data-testid="stMain"] [data-testid="stVerticalBlock"] {
        gap: 0 !important;
    }
    [data-testid="stMain"] div[data-testid="stVerticalBlockBorderWrapper"] {
        padding: 0 0.3rem !important;
        margin: 0 !important;
        min-height: 0 !important;
    }
    [data-testid="stMain"] div[data-testid="stVerticalBlockBorderWrapper"] > div {
        gap: 0 !important;
        min-height: 0 !important;
    }
    .tree-attrs {
        margin-top: 0.2rem;
        margin-bottom: 0.15rem;
    }

    [data-testid="stMain"] div[data-testid="stVerticalBlockBorderWrapper"] {
        margin-bottom: 0.08rem !important;
    }
    [data-testid="stMain"] div[data-testid="stVerticalBlockBorderWrapper"] button[kind="tertiary"] {
        padding: 0 !important;
        margin: 0 !important;
        min-height: 0 !important;
        height: auto !important;
        line-height: 1.1 !important;
    }
    /* BLOCK E — Compare / BOM */
    [data-testid="stMain"] div[data-testid="stVerticalBlockBorderWrapper"] .stCheckbox {
        min-height: 0 !important;
        padding: 0 !important;
        margin: 0 !important;
    }

    [data-testid="stMain"] div[data-testid="stVerticalBlockBorderWrapper"] .stCheckbox label {
        min-height: 0 !important;
        font-size: 0.76rem !important;
        gap: 0.15rem !important;
    }

    [data-testid="stMain"] div[data-testid="stVerticalBlockBorderWrapper"] .stCheckbox label p {
        font-size: 0.76rem !important;
        margin: 0 !important;
        white-space: nowrap !important;
    }


    /* BLOCK F — Attribute headers + plain text */
    .category-section {
        margin: 0.15rem 0 0.02rem 0;
        font-size: 0.75rem;
        font-weight: 600;
        opacity: 0.7;
    }

    .attr-simple {
        margin: 0 0 0.12rem 0.35rem;
        font-size: 0.84rem;
        line-height: 1.25;
    }

    /* BLOCK G — Data tables (shading + ⋮ menu) */
    [data-testid="stMain"] div[data-testid="stDataFrame"] {
        margin-bottom: 0.15rem !important;
    }

    [data-testid="stMain"] [data-testid="stDataFrame"],
    [data-testid="stMain"] [data-testid="stDataFrame"] * {
        user-select: text !important;
        -webkit-user-select: text !important;
        cursor: text;
    }
    [data-testid="stMain"] .stCaption {
        margin: 0.1rem 0 0 0 !important;
        padding: 0 !important;
    }
    [data-testid="stMain"] div[data-testid="stVerticalBlockBorderWrapper"] {
        margin-bottom: 0.15rem !important;
    }
    /* Make tree node buttons left-aligned (stop the floating centered label) */
    [data-testid="stMain"] div[data-testid="stVerticalBlockBorderWrapper"] button[kind="tertiary"] {
        justify-content: flex-start !important;
        text-align: left !important;
    }
    
    /* Also force any nested label containers left */
    [data-testid="stMain"] div[data-testid="stVerticalBlockBorderWrapper"] button[kind="tertiary"] * {
        justify-content: flex-start !important;
        text-align: left !important;
    }
    
    </style>
    """,
    unsafe_allow_html=True,
)

# =============================================================================
# SECTION 3 — NEO4J CONNECTION
# =============================================================================
@st.cache_resource
def get_driver() -> Driver:
    return GraphDatabase.driver(
        st.secrets["NEO4J_URI"].strip(),
        auth=(
            st.secrets["NEO4J_USERNAME"].strip(),
            st.secrets["NEO4J_PASSWORD"].strip(),
        ),
    )


driver = get_driver()


def run_query(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with driver.session() as session:
        return [r.data() for r in session.run(query, params or {})]


# =============================================================================
# SECTION 4 — PROPERTY PARSING
# =============================================================================
def parse_stored(v: Any) -> Any:
    if isinstance(v, str):
        s = v.strip()
        if s and s[0] in "{[":
            try:
                return json.loads(s)
            except json.JSONDecodeError:
                pass
    return v


def parse_props(props: dict[str, Any] | None) -> dict[str, Any]:
    return {k: parse_stored(v) for k, v in (props or {}).items()}

def attr_blocks(
    props: dict[str, Any] | None,
    filter_block: str | None = None,
) -> dict[str, Any]:
    parsed = parse_props(props)
    if filter_block:
        val = parsed.get(filter_block)
        return {filter_block: val} if val not in (None, "", {}, []) else {}

    return {
        k: parsed[k]
        for k in ATTR_BLOCKS
        if k in parsed and parsed[k] not in (None, "", {}, [])
    }
def has_attr_block(props, block: str) -> bool:
    return bool(attr_blocks(props, filter_block=block))

def _flatten_obj(
    obj: Any,
    prefix: str = "",
    *,
    combine_value_unit: bool = False,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    if isinstance(obj, dict):
        if (
            combine_value_unit
            and "value" in obj
            and obj.get("value") not in (None, "")
        ):
            value = str(obj["value"]).strip()
            unit = str(obj.get("unit", "")).strip()
            rows.append({"attribute": prefix, "value": f"{value} {unit}".strip()})
            return rows

        for k, v in obj.items():
            if combine_value_unit and k in {"unit", "flow", "compartment"}:
                continue
            path = f"{prefix}.{k}" if prefix else k
            rows.extend(
                _flatten_obj(v, path, combine_value_unit=combine_value_unit)
            )
        return rows

    if isinstance(obj, list):
        if obj and all(not isinstance(x, (dict, list)) for x in obj):
            rows.append(
                {"attribute": prefix, "value": ", ".join(str(x) for x in obj)}
            )
        else:
            for i, item in enumerate(obj):
                rows.extend(
                    _flatten_obj(
                        item,
                        f"{prefix}[{i}]",
                        combine_value_unit=combine_value_unit,
                    )
                )
        return rows

    if obj not in (None, ""):
        rows.append({"attribute": prefix, "value": str(obj)})

    return rows


def flatten_blocks(
    blocks: dict[str, Any],
    *,
    combine_value_unit: bool = False,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name, val in blocks.items():
        rows.extend(
            _flatten_obj(val, name, combine_value_unit=combine_value_unit)
        )
    if combine_value_unit:
        seen: set[tuple[str, str]] = set()
        deduped = []
        for row in rows:
            key = (row["attribute"], row["value"])
            if key not in seen:
                seen.add(key)
                deduped.append(row)
        return deduped
    return rows

def attrs_to_wide_row(rows: list[dict[str, str]]) -> dict[str, str]:
    return {r["attribute"]: r["value"] for r in rows}





def node_name(node: dict[str, Any]) -> str:
    props = parse_props(node.get("props"))
    return props.get("name") or node.get("label") or node.get("id") or "Unknown"


# =============================================================================
# SECTION 5 — NEO4J FETCHES
# =============================================================================
def upload_submission_to_repo(
    filename: str,
    file_bytes: bytes,
    email: str,
) -> None:

    owner = st.secrets["GITHUB_OWNER"]
    repo = st.secrets["GITHUB_REPO"]
    token = st.secrets["GITHUB_TOKEN"]

    path = f"incoming/{email}/{filename}"

    url = (
        f"https://api.github.com/repos/"
        f"{owner}/{repo}/contents/{path}"
    )

    payload = {
        "message": f"submission {filename}",
        "content": base64.b64encode(file_bytes).decode("utf-8"),
        "branch": "main",
    }

    r = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json=payload,
        timeout=60,
    )

    if r.status_code not in (200, 201):
        raise RuntimeError(r.text)
        
def get_root_nodes() -> list[dict[str, str]]:
    return run_query(
        f"""
        MATCH (n:{NODE_LABEL})
        WHERE NOT ()-[:{CHILD_REL}]->(n)
        RETURN n.id AS id, n.name AS label
        ORDER BY label
        """
    )


@st.cache_data(show_spinner=False)
def fetch_root_subtree(root_id: str) -> list[dict[str, Any]]:
    return run_query(
        f"""
        MATCH (root:{NODE_LABEL} {{id:$root_id}})
    
        MATCH p=(root)-[:{CHILD_REL}*0..]->(n:{NODE_LABEL})
    
        WITH n, min(length(p)) AS depth
    
        OPTIONAL MATCH (parent:{NODE_LABEL})-[:{CHILD_REL}]->(n)
    
        RETURN
            n.id AS id,
            n.name AS label,
            properties(n) AS props,
            depth,
            parent.id AS parent_id
    
        ORDER BY depth, label
        """,
        {"root_id": root_id},
    )

@st.cache_data(show_spinner=False)
def fetch_material_node(material_id: str) -> dict[str, Any] | None:
    rows = run_query(
        f"""
        MATCH (n:{NODE_LABEL} {{id: $material_id}})
        RETURN
            n.id AS id,
            n.name AS label,
            properties(n) AS props
        LIMIT 1
        """,
        {"material_id": material_id},
    )
    return rows[0] if rows else None
    
def search_materials(query: str) -> list[dict[str, Any]]:
    q = query.strip().lower()
    if not q:
        return []

    return run_query(
        f"""
        MATCH (n:{NODE_LABEL})
        WHERE toLower(coalesce(n.name, '')) CONTAINS $q
           OR toLower(coalesce(n.id, '')) CONTAINS $q
           OR toLower(coalesce(n.code, '')) CONTAINS $q

        OPTIONAL MATCH p = (root:{NODE_LABEL})-[:{CHILD_REL}*0..]->(n)
        WHERE NOT ()-[:{CHILD_REL}]->(root)

        WITH n, p, root,
             CASE
                 WHEN toLower(coalesce(n.name, '')) = $q THEN 0
                 WHEN toLower(coalesce(n.id, '')) = $q THEN 1
                 WHEN toLower(coalesce(n.code, '')) = $q THEN 2
                 WHEN toLower(coalesce(n.name, '')) STARTS WITH $q THEN 3
                 WHEN toLower(coalesce(n.id, '')) STARTS WITH $q THEN 4
                 WHEN toLower(coalesce(n.code, '')) STARTS WITH $q THEN 5
                 ELSE 6
             END AS rank_score

        RETURN
            n.id AS id,
            coalesce(n.name, n.id) AS label,
            root.id AS root_id
        ORDER BY rank_score, label
        """,
        {"q": q})


def upload_submission_to_repo(
    filename: str,
    file_bytes: bytes,
    email: str,
) -> None:

    owner = st.secrets["GITHUB_OWNER"]
    repo = st.secrets["GITHUB_REPO"]
    token = st.secrets["GITHUB_TOKEN"]

    path = f"incoming/{email}/{filename}"

    url = (
        f"https://api.github.com/repos/"
        f"{owner}/{repo}/contents/{path}"
    )

    payload = {
        "message": f"submission {filename}",
        "content": base64.b64encode(file_bytes).decode("utf-8"),
        "branch": "main",
    }

    r = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json=payload,
        timeout=60,
    )

    if r.status_code not in (200, 201):
        raise RuntimeError(r.text)


# =============================================================================
# SECTION 6 — IN-MEMORY INDEXES
# =============================================================================
# =============================================================================
# SECTION 6 — IN-MEMORY INDEXES
# =============================================================================
def build_submit_indexes() -> dict[str, Any]:
    node = {
        "id": SUBMIT_MATERIAL_ID,
        "label": "Submit your data",
        "props": {
            "name": "User participation",
            "participation": {
                "questions": [
                    "Email address",
                    "Your name",
                    "Data title",
                    "File",
                ]
            },
        },
        "depth": 0,
        "parent_id": None,
    }
    return {
        "root_id": SUBMIT_MATERIAL_ID,
        "root_name": "User participation",
        "rows": [node],
        "nodes_by_id": {SUBMIT_MATERIAL_ID: node},
        "children_by_parent": {},
        "parent_by_id": {SUBMIT_MATERIAL_ID: None},
        "depth_by_id": {SUBMIT_MATERIAL_ID: 0},
        "descendants_by_id": {SUBMIT_MATERIAL_ID: []},
    }

def build_subtree_indexes(rows: list[dict[str, Any]], root_id: str) -> dict[str, Any]:
    nodes_by_id: dict[str, dict[str, Any]] = {}
    children_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    parent_by_id: dict[str, str | None] = {}
    depth_by_id: dict[str, int] = {}

    for row in rows:
        node = {
            "id": row["id"],
            "label": row["label"],
            "props": row["props"],
            "depth": row["depth"],
            "parent_id": row["parent_id"],
        }
        nodes_by_id[row["id"]] = node
        parent_by_id[row["id"]] = row["parent_id"]
        depth_by_id[row["id"]] = row["depth"]

    for row in rows:
        parent_id = row["parent_id"]
        if parent_id is not None:
            children_by_parent[parent_id].append(nodes_by_id[row["id"]])

    for _, children in children_by_parent.items():
        children.sort(key=node_name)

    descendants_by_id: dict[str, list[str]] = {}
    for node_id in nodes_by_id:
        out: list[str] = []
        queue = deque(child["id"] for child in children_by_parent.get(node_id, []))
        while queue:
            current = queue.popleft()
            out.append(current)
            queue.extend(child["id"] for child in children_by_parent.get(current, []))
        descendants_by_id[node_id] = out

    root_node = nodes_by_id[root_id]
    root_name = node_name(root_node)

    return {
        "root_id": root_id,
        "root_name": root_name,
        "rows": rows,
        "nodes_by_id": nodes_by_id,
        "children_by_parent": children_by_parent,
        "parent_by_id": parent_by_id,
        "depth_by_id": depth_by_id,
        "descendants_by_id": descendants_by_id,
    }


def get_path_labels_from_indexes(
    path_ids: list[str],
    nodes_by_id: dict[str, dict[str, Any]],
) -> list[str]:
    return [node_name(nodes_by_id[node_id]) for node_id in path_ids if node_id in nodes_by_id]


def get_subtree_rows_from_indexes(node_id: str, indexes: dict[str, Any]) -> list[dict[str, Any]]:
    ids = [node_id] + indexes["descendants_by_id"].get(node_id, [])
    rows: list[dict[str, Any]] = []

    for descendant_id in ids:
        node = indexes["nodes_by_id"][descendant_id]
        rows.append(
            {
                "id": node["id"],
                "label": node["label"],
                "props": node["props"],
                "depth": node["depth"] - indexes["depth_by_id"][node_id],
            }
        )

    rows.sort(key=lambda r: (r["depth"], node_name(r)))
    return rows


# =============================================================================
# SECTION 7 — FILTER, COMPARE, AND CURRENT-NODE DISPLAY
# =============================================================================

def material_path_key(path_ids: list[str]) -> str:
    return PATH_SEP.join(path_ids)

def tree_widget_key(prefix: str, path_ids: list[str]) -> str:
    return f"{prefix}{PATH_SEP}{material_path_key(path_ids)}"
    
def all_node_data(node: dict[str, Any]) -> dict[str, Any]:
    """Every property on the node — no ATTR_BLOCKS filter."""
    return parse_props(node.get("props"))


def all_node_value_count(node: dict[str, Any]) -> int:
    return len(flatten_blocks(all_node_data(node)))


def siblings_of(indexes: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    parent_id = indexes["parent_by_id"].get(node_id)
    if parent_id is None:
        return []
    sibs = [
        n for n in indexes["children_by_parent"].get(parent_id, [])
        if n["id"] != node_id
    ]
    sibs.sort(key=node_name)
    return sibs

def collect_table_sections(
    prefix: str, obj: Any
) -> list[tuple[str, str | list[dict[str, Any]]]]:
    sections: list[tuple[str, str | list[dict[str, Any]]]] = []

    if obj in (None, "", {}, []):
        return sections

    if isinstance(obj, dict):
        scalar_map = {
            k: v for k, v in obj.items()
            if not isinstance(v, (dict, list)) and v not in (None, "")
        }
        complex_items = {
            k: v for k, v in obj.items()
            if isinstance(v, (dict, list)) and v not in (None, "", {}, [])
        }

        if scalar_map and not complex_items:
            sections.append((prefix, [scalar_map]))
            return sections

        if is_flat_dict(obj):
            sections.append((prefix, [obj]))
            return sections

        if scalar_map:
            sections.append((prefix, [scalar_map]))

        for k, v in complex_items.items():
            child = f"{prefix} → {k}" if prefix else k
            sections.extend(collect_table_sections(child, v))
        return sections

    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            sections.append((prefix, obj))
        elif obj and all(not isinstance(x, (dict, list)) for x in obj):
            sections.append((prefix, ", ".join(str(x) for x in obj)))
        else:
            for i, item in enumerate(obj):
                sections.extend(collect_table_sections(f"{prefix}[{i}]", item))
        return sections

    sections.append((prefix, str(obj)))
    return sections
    
def render_node_all_categories(node: dict[str, Any]) -> None:
    props = parse_props(node.get("props"))
    for block_name in ATTR_BLOCKS:
        if block_name not in props:
            continue
        block_val = props[block_name]
        if block_val in (None, "", {}, []):
            continue
        st.markdown(
    f"##### {block_name}",
)
        #f"### or f"#### to be smaller main attr keys
        render_nested(None, block_val)


def capped_tree_depth(depth: int) -> int:
    return min(max(depth, 0), MAX_INDENT_LEVELS)


def tree_indent_px(depth: int) -> int:
    return capped_tree_depth(depth) * INDENT_PX_PER_LEVEL


def tree_indent_fraction(depth: int) -> float:
    # attribute block column indent inside expanded node
    return min(capped_tree_depth(depth) * 0.075, MAX_CHILD_SPACER + 0.29)
    # 6 * 0.075 = 0.45, cap below ~0.53 you had before


def tree_child_spacer_fraction(depth: int) -> float:
    # spacer before recursive child render
    return min(capped_tree_depth(depth + 1) * CHILD_SPACER_PER_LEVEL, MAX_CHILD_SPACER)
    
def render_material_tree_node(
    indexes: dict[str, Any],
    node: dict[str, Any],
    depth: int = 0,
    path_ids: list[str] | None = None,
) -> None:
    node_id = node["id"]
    path_ids = list(path_ids or []) + [node_id]

    if not tree_node_visible(indexes, node_id):
        return

    cname = node_name(node)
    children = indexes["children_by_parent"].get(node_id, [])
    props = parse_props(node.get("props"))

    blocks = {
        k: v
        for k, v in props.items()
        if k not in META_KEYS and v not in (None, "", {}, [])
    }
    value_count = len(flatten_blocks(blocks)) if blocks else 0
    is_open = node_id in st.session_state.expanded_material_ids

    title = cname
    if children:
        title += f" ({len(children)} submaterials)"
    if value_count:
        title += f" [{value_count} values]"

    cmp_key = tree_widget_key("cmp_tree", path_ids)
    bom_key = tree_widget_key("bom_tree", path_ids)
    toggle_key = tree_widget_key("tree_toggle", path_ids)

    def toggle_expand() -> None:
        if node_id in st.session_state.expanded_material_ids:
            st.session_state.expanded_material_ids.discard(node_id)
        else:
            st.session_state.expanded_material_ids.add(node_id)

    indent_px = tree_indent_px(depth)
    st.markdown(f'<div style="margin-left:{indent_px}px;">', unsafe_allow_html=True)

    with st.container(border=True):
        compare_col, bom_col, _ = st.columns([1.5, 1.5, 7], gap="small", vertical_alignment="center")

        with compare_col:
            st.checkbox(
                "Compare",
                value=is_material_in_compare(node_id),
                key=cmp_key,
                on_change=on_compare_toggle,
                args=(node_id, cname, cmp_key),
            )

        with bom_col:
            st.checkbox(
                "BOM",
                value=is_in_bill(node_id),
                key=bom_key,
                on_change=on_bill_toggle,
                args=(node_id, bom_key),
            )

        arrow = "▼" if is_open else "▶"
        label = f":red[{arrow} {title}]" if is_open else f"{arrow} {title}"

        if st.button(label, key=toggle_key, use_container_width=True):
            toggle_expand()
            st.rerun()

        if is_open:
            if blocks:
                attr_indent = max(tree_indent_fraction(depth), 0.01)
                _, body = st.columns([attr_indent, 1.0 - attr_indent], gap="small")
                with body:
                    render_node_all_categories(node)

            for child in children:
                spacer_frac = max(tree_child_spacer_fraction(depth), 0.01)
                spacer, child_col = st.columns([spacer_frac, 1.0 - spacer_frac])
                with child_col:
                    render_material_tree_node(indexes, child, depth + 1, path_ids=path_ids)

    st.markdown("</div>", unsafe_allow_html=True)


def cell_to_display(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def render_nested(key: str | None, obj: Any, level: int = 0) -> None:

    if obj in (None, "", {}, []):
        return

    # ------------------------------------------------------------------
    # LIST OF DICTS
    # ------------------------------------------------------------------
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        df = pd.DataFrame(obj)

        first = [c for c in ["name", "amount", "unit"] if c in df.columns]
        rest = [c for c in df.columns if c not in first]

        df = df[first + rest]

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
        )
        return

    # ------------------------------------------------------------------
    # SCALAR LIST
    # ------------------------------------------------------------------
    if isinstance(obj, list) and obj and all(
        not isinstance(x, (dict, list)) for x in obj
    ):
        df = pd.DataFrame(
            [{"value": ", ".join(str(x) for x in obj)}]
        )

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
        )
        return

    # ------------------------------------------------------------------
    # DICT
    # ------------------------------------------------------------------
    if isinstance(obj, dict):

        # --------------------------------------------------------------
        # FLAT RECORD
        # --------------------------------------------------------------
        if all(
            not isinstance(v, (dict, list))
            for v in obj.values()
        ):
            df = pd.DataFrame([obj])

            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
            )
            return

        # --------------------------------------------------------------
        # PROPERTY TABLE
        #
        # compressive_strength -> {value,unit}
        # density -> {value,unit}
        # --------------------------------------------------------------
        if (
            obj
            and all(isinstance(v, dict) for v in obj.values())
            and all(
                any(
                    not isinstance(x, (dict, list))
                    for x in v.values()
                )
                for v in obj.values()
            )
        ):

            rows: list[dict[str, Any]] = []

            for prop, record in obj.items():

                row: dict[str, Any] = {
                    "property": prop
                }

                for k, v in record.items():

                    if isinstance(v, (dict, list)):
                        row[k] = json.dumps(
                            v,
                            ensure_ascii=False,
                        )
                    else:
                        row[k] = v

                rows.append(row)

            df = pd.DataFrame(rows)

            preferred = [
                "property",
                "name",
                "amount",
                "value",
                "unit",
            ]

            cols = (
                [c for c in preferred if c in df.columns]
                + [c for c in df.columns if c not in preferred]
            )

            df = df[cols]

            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
            )
            return

        # --------------------------------------------------------------
        # CONTAINER DICT
        #
        # activity
        #   exchanges
        #       production
        #       technosphere
        # --------------------------------------------------------------
        for child_key, child_val in obj.items():

            st.markdown(f"###### {child_key}")

            render_nested(
                child_key,
                child_val,
                level + 1,
            )

        return

    # ------------------------------------------------------------------
    # MIXED LIST
    # ------------------------------------------------------------------
    if isinstance(obj, list):

        for item in obj:
            render_nested(
                key,
                item,
                level + 1,
            )

        return

    # ------------------------------------------------------------------
    # FALLBACK
    # ------------------------------------------------------------------
    st.dataframe(
        pd.DataFrame(
            [{"value": str(obj)}]
        ),
        use_container_width=True,
        hide_index=True,
    )

def render_node_blocks(node: dict[str, Any]) -> None:
    blocks = attr_blocks(
        node.get("props"),
        filter_block=active_filter_block(),
    )
    if not blocks:
        block = active_filter_block()
        st.caption(f"No `{block}` data on this node." if block else "No attribute values on this node.")
        return

    for group_name, group_val in blocks.items():
        st.subheader(group_name)
        render_nested(None, group_val)
        
def node_passes_submaterial_filter(node: dict[str, Any]) -> bool:
    choice = st.session_state.filter_attr_block
    if choice == "(no filter)":
        return True
    return has_attr_block(node.get("props"), choice)

def filter_nodes_by_attr(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [n for n in nodes if node_passes_submaterial_filter(n)]


def visible_submaterials(indexes: dict[str, Any], parent_id: str) -> list[dict[str, Any]]:
    direct_children = indexes["children_by_parent"].get(parent_id, [])

    if st.session_state.filter_attr_block == "(no filter)":
        return direct_children

    visible: list[dict[str, Any]] = []

    for child in direct_children:
        if node_passes_submaterial_filter(child):
            visible.append(child)
        else:
            visible.extend(visible_submaterials(indexes, child["id"]))

    visible.sort(key=node_name)
    return visible


def path_to_node(indexes: dict[str, Any], target_id: str) -> list[str]:
    parent_by_id = indexes["parent_by_id"]
    chain: list[str] = []
    cur: str | None = target_id
    while cur is not None:
        chain.append(cur)
        cur = parent_by_id.get(cur)
    return list(reversed(chain))


def first_filtered_descendant(indexes: dict[str, Any], start_id: str) -> str | None:
    start_node = indexes["nodes_by_id"].get(start_id)
    if not start_node:
        return None
    if node_passes_submaterial_filter(start_node):
        return start_id
    queue = deque([start_id])
    while queue:
        nid = queue.popleft()
        for child in indexes["children_by_parent"].get(nid, []):
            if node_passes_submaterial_filter(child):
                return child["id"]
            queue.append(child["id"])
    return None


def apply_filter_auto_dive(indexes: dict[str, Any]) -> bool:
    if st.session_state.filter_attr_block == "(no filter)":
        return False
    if not st.session_state.path_ids:
        return False

    current_id = st.session_state.path_ids[-1]
    current_node = indexes["nodes_by_id"].get(current_id)

    # Current node has the filtered block → stay
    if current_node and node_passes_submaterial_filter(current_node):
        return False

    # Current node can show matching submaterials → stay
    if visible_submaterials(indexes, current_id):
        return False

    # Walk up to nearest ancestor that can show matches
    for candidate_id in reversed(st.session_state.path_ids[:-1]):
        if visible_submaterials(indexes, candidate_id):
            new_path = path_to_node(indexes, candidate_id)
            if new_path != st.session_state.path_ids:
                st.session_state.path_ids = new_path
                return True
            return False

    # Last resort: jump to root
    root_id = st.session_state.path_ids[0]
    if st.session_state.path_ids != [root_id]:
        st.session_state.path_ids = [root_id]
        return True

    return False



    
def node_has_values(node: dict[str, Any]) -> bool:
    return bool(flatten_blocks(attr_blocks(node.get("props"))))
    
def active_filter_block() -> str | None:
    choice = st.session_state.get("filter_attr_block", "(no filter)")
    return None if choice == "(no filter)" else choice




def summarize_branch(indexes: dict[str, Any], node_id: str) -> dict[str, Any]:
    direct_children = indexes["children_by_parent"].get(node_id, [])
    descendant_ids = indexes["descendants_by_id"].get(node_id, [])

    populated_direct_children = [
        child for child in direct_children if node_has_values(child)
    ]

    populated_descendants = [
        indexes["nodes_by_id"][desc_id]
        for desc_id in descendant_ids
        if node_has_values(indexes["nodes_by_id"][desc_id])
    ]

    return {
        "direct_children": direct_children,
        "direct_child_count": len(direct_children),
        "descendant_count": len(descendant_ids),
        "populated_direct_children": populated_direct_children,
        "populated_descendant_count": len(populated_descendants),
    }
#compare checkbox
def part_compare_key(material_id: str, attribute: str) -> str:
    return f"{material_id}|{attribute}"


def is_part_in_compare(material_id: str, attribute: str) -> bool:
    return any(
        p["key"] == part_compare_key(material_id, attribute)
        for p in st.session_state.compare_parts
    )

def add_part_to_compare(
    material_id: str, material_name: str, attribute: str, value: str
) -> None:
    entry = {
        "key": part_compare_key(material_id, attribute),
        "material_id": material_id,
        "material_name": material_name,
        "attribute": attribute,
        "value": value,
    }
    if not any(p["key"] == entry["key"] for p in st.session_state.compare_parts):
        st.session_state.compare_parts.append(entry)


def remove_part_from_compare(key: str) -> None:
    st.session_state.compare_parts = [
        p for p in st.session_state.compare_parts if p["key"] != key
    ]


def is_material_in_compare(material_id: str) -> bool:
    return any(m["id"] == material_id for m in st.session_state.compare_materials)


def add_material_to_compare(material_id: str, material_name: str, category: str) -> None:
    if not is_material_in_compare(material_id):
        st.session_state.compare_materials.append(
            {"id": material_id, "name": material_name, "category": category}
        )


def remove_material_from_compare(material_id: str) -> None:
    st.session_state.compare_materials = [
        m for m in st.session_state.compare_materials if m["id"] != material_id
    ]
    for key in list(st.session_state.keys()):
        if key.startswith("cmp_tree/") and key.endswith(f"/{material_id}"):
            st.session_state[key] = False
        elif key == f"cmp_tree/{material_id}":
            st.session_state[key] = False

def on_compare_toggle(material_id: str, material_name: str, widget_key: str) -> None:
    if st.session_state[widget_key]:
        indexes = st.session_state.get("root_indexes")
        category = indexes["root_name"] if indexes else "Unknown"
        add_material_to_compare(material_id, material_name, category)
        st.session_state.show_compare_view = True
    else:
        remove_material_from_compare(material_id)
        if not st.session_state.compare_materials:
            st.session_state.show_compare_view = False


def render_material_compare(
    materials: list[dict[str, Any]]
) -> None:

    def collect_compare_values(
        obj: Any,
        prefix: str,
        out: dict[str, list[str]],
    ) -> None:

        if obj in (None, "", {}, []):
            return

        if isinstance(obj, dict):

            if "value" in obj:
                value = str(obj["value"])

                if obj.get("unit"):
                    value += f" {obj['unit']}"

                out.setdefault(prefix, []).append(value)
                return

            for k, v in obj.items():

                if k in {
                    "id",
                    "code",
                    "vector",
                    "placement",
                    "name",
                }:
                    continue

                child = (
                    f"{prefix}.{k}"
                    if prefix
                    else k
                )

                collect_compare_values(
                    v,
                    child,
                    out,
                )

            return

        if isinstance(obj, list):

            for item in obj:

                collect_compare_values(
                    item,
                    prefix,
                    out,
                )

            return

        out.setdefault(
            prefix,
            []
        ).append(str(obj))

    # ==========================================================
    # ENGINEERING
    # ==========================================================

    st.markdown("### Engineering")

    engineering_rows = {}

    for material in materials:

        material_name = material["name"]

        engineering = material["props"].get(
            "engineering",
            {}
        )

        rows = flatten_blocks(
            {"engineering": engineering},
            combine_value_unit=True,
        )

        for row in rows:

            attr = (
                row["attribute"]
                .replace("engineering.", "")
            )

            if attr not in engineering_rows:
                engineering_rows[attr] = {
                    "Attribute": attr.split(".")[-1]
                    .replace("_", " ")
                    .title()
                }

            engineering_rows[attr][material_name] = row["value"]

    if engineering_rows:

        engineering_df = pd.DataFrame(
            engineering_rows.values()
        )

        st.dataframe(
            engineering_df,
            use_container_width=True,
            hide_index=True,
            height=400,
        )

    # ==========================================================
    # LCIA
    # ==========================================================

    st.markdown("### LCIA")

    lcia_rows = {}

    for material in materials:

        material_name = material["name"]

        lcia = material["props"].get(
            "lcia",
            {}
        )

        rows = flatten_blocks(
            {"lcia": lcia},
            combine_value_unit=True,
        )

        for row in rows:

            attr = (
                row["attribute"]
                .replace("lcia.", "")
            )

            if attr not in lcia_rows:
                lcia_rows[attr] = {
                    "Attribute": attr.split(".")[-1]
                    .replace("_", " ")
                    .title()
                }

            lcia_rows[attr][material_name] = row["value"]

    if lcia_rows:

        lcia_df = pd.DataFrame(
            lcia_rows.values()
        )

        st.dataframe(
            lcia_df,
            use_container_width=True,
            hide_index=True,
            height=400,
        )

    # ==========================================================
    # ACTIVITY
    # ==========================================================

    st.markdown("### Activity")

    exchange_types = [
        "production",
        "technosphere",
        "biosphere",
        "transport",
        "cost",
    ]

    for exchange_type in exchange_types:

        inventory_rows = {}

        for material in materials:

            material_name = material["name"]

            exchanges = (
                material["props"]
                .get("activity", {})
                .get("exchanges", {})
            )

            inventory = exchanges.get(
                exchange_type,
                []
            )

            if isinstance(inventory, dict):
                inventory = [inventory]

            for record in inventory:

                if not isinstance(record, dict):
                    continue

                row_name = (
                    record.get("name")
                    or record.get("input")
                    or record.get("flow")
                    or "Unnamed"
                )

                if row_name not in inventory_rows:

                    inventory_rows[row_name] = {
                        "Name": row_name,
                        "Unit": record.get(
                            "unit",
                            "",
                        ),
                    }

                amount = record.get(
                    "amount",
                    ""
                )

                inventory_rows[row_name][
                    material_name
                ] = amount

        if inventory_rows:

            st.markdown(
                f"#### {exchange_type.title()}"
            )

            inventory_df = pd.DataFrame(
                inventory_rows.values()
            )

            st.dataframe(
                inventory_df,
                use_container_width=True,
                hide_index=True,
                height=350,
            )
 ###
    material_maps = {}

    all_attributes = set()

    for material in materials:

        values = {}

        collect_compare_values(
            material["props"],
            "",
            values,
        )

        material_maps[
            material["name"]
        ] = values

        all_attributes.update(
            values.keys()
        )

    common_attributes = []
    unique_attributes = []

    for attr in sorted(all_attributes):

        present_count = sum(
            1
            for material_name in material_maps
            if attr in material_maps[material_name]
        )

        if present_count == len(material_maps):
            common_attributes.append(attr)
        else:
            unique_attributes.append(attr)

    ordered_attributes = (
        common_attributes
        + unique_attributes
    )

    compare_rows = []

    for attr in ordered_attributes:

        row = {
            "Attribute": (
                attr.replace("_", " ")
                .replace(".", " → ")
                .title()
            )
        }

        for material_name in material_maps:

            values = material_maps[
                material_name
            ].get(
                attr,
                [],
            )

            row[
                material_name
            ] = "\n".join(
                str(v)
                for v in values
            )

        compare_rows.append(row)

    df = pd.DataFrame(compare_rows)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=900,
    )

    st.download_button(
        "Download comparison (CSV)",
        df.to_csv(index=False),
        file_name="material_comparison.csv",
        mime="text/csv",
    )


def on_nav_child(child_id: str) -> None:
    indexes = st.session_state.get("root_indexes")
    if not indexes or not st.session_state.path_ids:
        return

    current_id = st.session_state.path_ids[-1]
    allowed_ids = {n["id"] for n in visible_submaterials(indexes, current_id)}

    if child_id in allowed_ids:
        st.session_state.path_ids = path_to_node(indexes, child_id)
        st.rerun()

def tree_node_visible(indexes: dict[str, Any], node_id: str) -> bool:
    if st.session_state.filter_attr_block == "(no filter)":
        return True
    node = indexes["nodes_by_id"].get(node_id)
    if node and node_passes_submaterial_filter(node):
        return True
    return any(
        tree_node_visible(indexes, child["id"])
        for child in indexes["children_by_parent"].get(node_id, [])
    )


           

# =============================================================================
# SECTION 8 — BOM HELPERS
# =============================================================================
def collect_subtree_ids(
    indexes: dict[str, Any],
    node_id: str,
) -> list[str]:

    ids = [node_id]

    for child in indexes["children_by_parent"].get(
        node_id,
        [],
    ):
        ids.extend(
            collect_subtree_ids(
                indexes,
                child["id"],
            )
        )

    return ids
    
def is_in_bill(material_id: str) -> bool:
    for items in st.session_state.bom.values():
        if any(b["id"] == material_id for b in items):
            return True
    return False


def add_to_bill_from_node(node: dict[str, Any], category: str) -> None:
    attr_rows = flatten_blocks(attr_blocks(node.get("props")))
    entry = {
        "id": node["id"],
        "name": node_name(node),
        "values": attrs_to_wide_row(attr_rows),
    }
    st.session_state.bom.setdefault(category, [])
    if not any(b["id"] == node["id"] for b in st.session_state.bom[category]):
        st.session_state.bom[category].append(entry)


def remove_from_bill(material_id: str) -> None:
    for cat, items in list(st.session_state.bom.items()):
        st.session_state.bom[cat] = [b for b in items if b["id"] != material_id]
        if not st.session_state.bom[cat]:
            del st.session_state.bom[cat]


def on_bill_toggle(
    material_id: str,
    widget_key: str,
) -> None:

    indexes = st.session_state.get(
        "root_indexes"
    )

    if not indexes:
        return

    checked = st.session_state[widget_key]

    children = indexes[
        "children_by_parent"
    ].get(
        material_id,
        [],
    )

    if children:

        subtree_ids = collect_subtree_ids(
            indexes,
            material_id,
        )

        for nid in subtree_ids:

            node = indexes["nodes_by_id"][nid]

            if checked:
                add_to_bill_from_node(
                    node,
                    indexes["root_name"],
                )
            else:
                remove_from_bill(
                    nid,
                )

    else:

        node = indexes["nodes_by_id"][
            material_id
        ]

        if checked:
            add_to_bill_from_node(
                node,
                indexes["root_name"],
            )
        else:
            remove_from_bill(
                material_id,
            )

# =============================================================================
# SECTION 9 — NAVIGATION
# =============================================================================
def on_crumb_click(idx: int) -> None:
    if st.session_state.path_ids and 0 <= idx < len(st.session_state.path_ids):
        st.session_state.path_ids = st.session_state.path_ids[: idx + 1]


def render_clickable_path(path_ids: list[str], indexes: dict[str, Any]) -> None:
    labels = get_path_labels_from_indexes(path_ids, indexes["nodes_by_id"])
    if not labels:
        return

    st.caption("Path - navigate center-view submaterials")
    for i, label in enumerate(labels):
        st.button(
            label,
            key=f"crumb_{path_ids[i]}_{i}",
            on_click=on_crumb_click,
            args=(i,),
            use_container_width=True,
        )

def html_escape(value: Any) -> str:
    s = "" if value is None else str(value)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )


def build_bom_dataframe() -> pd.DataFrame:
    bom_rows: list[dict[str, str]] = []
    for category in sorted(st.session_state.bom.keys()):
        for item in st.session_state.bom[category]:
            node = fetch_material_node(item["id"])
            if not node:
                continue
            row = {
                "category": category,
                "material_id": item["id"],
                "material_name": item["name"],
            }
            props = parse_props(node.get("props"))
            for attr_row in flatten_blocks(props):
                row[attr_row["attribute"]] = attr_row["value"]
            bom_rows.append(row)
    if not bom_rows:
        return pd.DataFrame()
    bom_df = pd.DataFrame(bom_rows)
    fixed_cols = ["category", "material_id", "material_name"]
    other_cols = [c for c in bom_df.columns if c not in fixed_cols]

    return bom_df[fixed_cols + sorted(other_cols)]

def filter_bom_dataframe(
    bom_df: pd.DataFrame,
    selected_categories: list[str],
    selected_materials: list[str],
    selected_attributes: list[str],
    attribute_mode: str,
) -> pd.DataFrame:
    filtered = bom_df.copy()

    if selected_categories:
        filtered = filtered[filtered["category"].isin(selected_categories)]

    if selected_materials:
        filtered = filtered[filtered["material_name"].isin(selected_materials)]

    if selected_attributes:
        existing_selected_attributes = [a for a in selected_attributes if a in filtered.columns]

        if existing_selected_attributes:
            if attribute_mode == "any selected attribute":
                mask = filtered[existing_selected_attributes].notna().any(axis=1)
                filtered = filtered[mask]
            elif attribute_mode == "all selected attributes":
                mask = filtered[existing_selected_attributes].notna().all(axis=1)
                filtered = filtered[mask]

            fixed_cols = ["category", "material_id", "material_name"]
            filtered = filtered[fixed_cols + existing_selected_attributes]

    return filtered

# =============================================================================
# SECTION 10 — SESSION STATE
# =============================================================================

if "has_searched" not in st.session_state:
    st.session_state.has_searched = False
    st.session_state.path_ids = []
    st.session_state.root_indexes = None
    st.session_state.search_feedback = ""

if "search_results" not in st.session_state:
    st.session_state.search_results = []

if "nav_target_id" not in st.session_state:
    st.session_state.nav_target_id = None

if "bom" not in st.session_state:
    st.session_state.bom = {}

if "filter_attr_block" not in st.session_state:
    st.session_state.filter_attr_block = "(no filter)"

if "compare_parts" not in st.session_state:
    st.session_state.compare_parts = []

if "compare_materials" not in st.session_state:
    st.session_state.compare_materials = []

if "show_compare_view" not in st.session_state:
    st.session_state.show_compare_view = False
    
if "expanded_material_ids" not in st.session_state:
    st.session_state.expanded_material_ids = set()
elif isinstance(st.session_state.expanded_material_ids, list):
    st.session_state.expanded_material_ids = set(st.session_state.expanded_material_ids)

if "show_submit_tab" not in st.session_state:
    st.session_state.show_submit_tab = False

if "submit_email" not in st.session_state:
    st.session_state.submit_email = ""
    
if "active_main_tab" not in st.session_state:
    st.session_state.active_main_tab = "Path + explore"

if "submit_answers" not in st.session_state:
    st.session_state.submit_answers = {}
# =============================================================================
# SECTION 11 — APP STARTUP
# =============================================================================
st.markdown(
    '<p class="app-title">Material Ontology Explorer</p>',
    unsafe_allow_html=True,
)

# roots dropdown
roots = get_root_nodes()
root_map = {r["id"]: r["label"] for r in roots}
browse_options = [""] + list(root_map.keys())

current_root_id = (
    st.session_state.path_ids[0]
    if st.session_state.path_ids and st.session_state.path_ids[0] in root_map
    else ""
)

browse_pick = st.selectbox(
    "Main Category",
    options=browse_options,
    index=browse_options.index(current_root_id)
    if current_root_id in browse_options
    else 0,
    format_func=lambda rid: "— select —" if rid == "" else root_map[rid],
)

if browse_pick != current_root_id:
    if browse_pick:
        st.session_state.has_searched = True
        st.session_state.path_ids = [browse_pick]
        st.session_state.root_indexes = None
        st.session_state.nav_target_id = None
        st.session_state.search_feedback = ""
        st.session_state.search_results = []
    else:
        st.session_state.has_searched = False
        st.session_state.path_ids = []
        st.session_state.root_indexes = None
        st.session_state.nav_target_id = None
        st.session_state.search_feedback = ""
        st.session_state.search_results = []

    st.rerun()
    #end dropdown

# =============================================================================
# SECTION 12 — SIDEBAR
# =============================================================================
with st.sidebar:
    if st.button("Add data", key="sidebar_add_data", use_container_width=True):
        st.session_state.has_searched = True
        st.session_state.path_ids = [SUBMIT_MATERIAL_ID]
        st.session_state.root_indexes = build_submit_indexes()
        st.session_state.submit_answers = {}
        st.session_state.active_main_tab = "Submit data"
        st.rerun()
    if st.button("Clear", use_container_width=True):
        st.session_state.has_searched = False
        st.session_state.path_ids = []
        st.session_state.root_indexes = None
        st.session_state.nav_target_id = None
        st.session_state.search_query = ""
        st.session_state.search_feedback = ""
        st.session_state.search_results = []
        st.session_state.filter_attr_block = "(no filter)"
        st.session_state.compare_parts = []
        st.session_state.compare_materials = []
        st.session_state.show_compare_view = False
        st.session_state.bom = {}
        st.session_state.expanded_material_ids = set()
        st.session_state.submit_answers = {}
        st.session_state.active_main_tab = "Path + explore"
        st.rerun()
    st.header("Navigation")
        
    with st.form("global_material_search", clear_on_submit=False):
        search_query = st.text_input("query", placeholder="", label_visibility="collapsed")
        search_submitted = st.form_submit_button("Search")

    if search_submitted:
        q = search_query.strip()
        if not q:
            st.session_state.search_results = []
            st.session_state.search_feedback = "Enter a search term."
        else:
            seen: set[str] = set()
            unique: list[dict[str, Any]] = []
            for hit in search_materials(q):
                hid = hit.get("id")
                if hid and hid not in seen:
                    seen.add(hid)
                    unique.append(hit)
            st.session_state.search_results = unique
            if unique:
                st.session_state.search_feedback = (
                    f"{len(unique)} match(es) for “{q}”."
                )
            else:
                st.session_state.search_feedback = f"No materials found for “{q}”."

    if st.session_state.search_feedback:
        st.caption(st.session_state.search_feedback)

    if st.session_state.search_results:
        st.markdown("**Search results**")
        for i, hit in enumerate(st.session_state.search_results):
            label = hit.get("label") or hit.get("id") or "Unknown"
            if st.button(
                label,
                key=f"search_pick_{i}_{hit['id']}",
                use_container_width=True,
            ):
                root_id = hit.get("root_id")
                if not root_id:
                    st.session_state.search_feedback = f"No root found for {label}."
                #
                else:
                    st.session_state.has_searched = True               
                    st.session_state.root_indexes = None
                    st.session_state.nav_target_id = hit["id"]
                    st.session_state.path_ids = [root_id]
                    st.session_state.expanded_material_ids = set()
                    st.rerun()

    filter_pick = st.selectbox(
        "Only show submaterials with:",
        options=FILTER_ATTR_OPTIONS,
        index=FILTER_ATTR_OPTIONS.index(st.session_state.filter_attr_block)
        if st.session_state.filter_attr_block in FILTER_ATTR_OPTIONS
        else 0,
    )

    if filter_pick != st.session_state.filter_attr_block:
        st.session_state.filter_attr_block = filter_pick
        st.session_state.root_indexes = None
        st.rerun()

    if st.session_state.has_searched:
        root_id = st.session_state.path_ids[0]
        if root_id == SUBMIT_MATERIAL_ID:
            st.session_state.root_indexes = build_submit_indexes()
        else:
            root_rows = fetch_root_subtree(root_id)
            st.session_state.root_indexes = build_subtree_indexes(root_rows, root_id)
        indexes = st.session_state.root_indexes
        #
        target = (
            st.session_state.nav_target_id
            or st.session_state.path_ids[-1]
        )
        #
        if st.session_state.nav_target_id:
        
            target = st.session_state.nav_target_id
        
            path = path_to_node(
                indexes,
                target,
            )
        
            st.session_state.path_ids = path
        
            st.session_state.expanded_material_ids = set(path)
        
            st.session_state.nav_target_id = None

        if apply_filter_auto_dive(indexes):
            st.rerun()
        render_clickable_path(st.session_state.path_ids, indexes)

    if st.session_state.compare_materials:
        st.divider()
        st.markdown("**Compare List**")
        compare_by_cat: dict[str, list[dict[str, str]]] = defaultdict(list)
        for m in st.session_state.compare_materials:
            compare_by_cat[m.get("category", "Unknown")].append(m)
        for cat in sorted(compare_by_cat.keys()):
            st.markdown(f"**{cat}**")
            for m in compare_by_cat[cat]:
                name_col, reject_col = st.columns([9, 1], vertical_alignment="center")
                with name_col:
                    st.caption(f"• {m['name']}")
                with reject_col:
                    if st.button("×", key=f"reject_compare_{m['id']}", type="tertiary"):
                        remove_material_from_compare(m["id"])
                        st.rerun()
        if st.button("Clear compare list", use_container_width=True):
            st.session_state.compare_materials = []
            st.session_state.compare_parts = []
            st.session_state.show_compare_view = False
            st.rerun()

    st.divider()
    st.caption("Bill of materials")
    if not st.session_state.bom:
        st.caption("Empty.")
    else:
        for cat in sorted(st.session_state.bom.keys()):
            st.markdown(f"**{cat}**")
            for item in st.session_state.bom[cat]:
                name_col, reject_col = st.columns([9, 1], vertical_alignment="center")
                with name_col:
                    st.caption(f"• {item['name']}")
                with reject_col:
                    if st.button("×", key=f"reject_bom_{cat}_{item['id']}", type="tertiary"):
                        remove_from_bill(item["id"])
                        st.rerun()

    if st.button("Clear bill", use_container_width=True):
        st.session_state.bom = {}
        st.rerun()


# =============================================================================
# SECTION 13 — MAIN AREA GATE
# =============================================================================
if not st.session_state.has_searched or not st.session_state.path_ids:
    st.info("Search for a material by name, id, or code to get started.")
    st.stop()
    
if not st.session_state.root_indexes:
    st.info("Loading material data…")
    st.stop()

indexes = st.session_state.root_indexes
current_id = st.session_state.path_ids[-1]
node = indexes["nodes_by_id"].get(current_id)

if not node:
    st.error("Could not load this material.")
    st.stop()

direct_children = indexes["children_by_parent"].get(current_id, [])
subtree = get_subtree_rows_from_indexes(current_id, indexes)
    

# =============================================================================
# SECTION 14 — MAIN TABS
# =============================================================================

if current_id == SUBMIT_MATERIAL_ID:
    tab_submit, tab_path, tab_compare, tab_bom = st.tabs(
        ["Submit data", "Path + explore", "Compare", "Export BOM"]
    )
else:
    tab_path, tab_compare, tab_bom, tab_submit = st.tabs(
        ["Path + explore", "Compare", "Export BOM", "Submit data"]
    )

with tab_path:
    if current_id == SUBMIT_MATERIAL_ID:
        st.info("Use the **Submit data** tab.")
    else:
        root_id = st.session_state.path_ids[0]
        root_node = indexes["nodes_by_id"][root_id]
        render_material_tree_node(indexes, root_node, depth=0, path_ids=[])

with tab_compare:
    st.subheader("Compare materials")

    if len(st.session_state.compare_materials) < 2:
        branch = summarize_branch(indexes, current_id)

        if branch["direct_child_count"] == 0:
            st.info("Select at least 2 materials with the Compare checkbox.")
        else:
            st.info(
                f"{node_name(node)} is a category node with "
                f"{branch['direct_child_count']} direct submaterials and "
                f"{branch['populated_descendant_count']} populated descendants."
            )

            if branch["populated_direct_children"]:
                if st.button("Compare direct submaterials"):
                    st.session_state.compare_materials = [
                        {
                            "id": child["id"],
                            "name": node_name(child),
                            "category": indexes["root_name"],
                        }
                        for child in branch["populated_direct_children"]
                    ]
                    st.rerun()
            else:
                st.caption(
                    "No direct submaterials under this node have comparable values."
                )
    else:
        compare_material_nodes = []

        for material in st.session_state.compare_materials:
            material_node = fetch_material_node(material["id"])
            if not material_node:
                continue

            compare_material_nodes.append(
                {
                    "id": material["id"],
                    "name": material["name"],
                    "props": parse_props(material_node.get("props")),
                }
            )

        if not compare_material_nodes:
            st.info("No comparable materials found.")
        else:
            render_material_compare(compare_material_nodes)

with tab_bom:
    st.subheader("Export BOM")

    bom_df = build_bom_dataframe()

    if bom_df.empty:
        st.info("No materials in the bill of materials yet.")
    else:
        fixed_cols = ["category", "material_id", "material_name"]
        attr_cols = [c for c in bom_df.columns if c not in fixed_cols]

        selected_categories = st.multiselect(
            "Filter categories",
            options=sorted(bom_df["category"].dropna().unique().tolist()),
        )

        selected_materials = st.multiselect(
            "Filter materials",
            options=sorted(bom_df["material_name"].dropna().unique().tolist()),
        )

        selected_attributes = st.multiselect(
            "Keep only these attributes in table/export",
            options=sorted(attr_cols),
        )

        attribute_mode = st.radio(
            "Attribute row filter",
            options=["no row filter", "any selected attribute", "all selected attributes"],
            horizontal=True,
        )

        filtered_bom_df = filter_bom_dataframe(
            bom_df=bom_df,
            selected_categories=selected_categories,
            selected_materials=selected_materials,
            selected_attributes=selected_attributes,
            attribute_mode=attribute_mode,
        )

        st.dataframe(
            filtered_bom_df,
            use_container_width=True,
            hide_index=True,
            height=700,
        )

        bom_export_name = st.text_input(
            "Export file name",
            value="bill_of_materials_filtered",
            key="bom_export_name",
            help="Enter the CSV file name without .csv",
        ).strip()

        if not bom_export_name:
            bom_export_name = "bill_of_materials_filtered"

        if not bom_export_name.lower().endswith(".csv"):
            bom_export_name = f"{bom_export_name}.csv"

        st.download_button(
            "Export filtered BOM to CSV",
            filtered_bom_df.to_csv(index=False),
            file_name=bom_export_name,
            mime="text/csv",
        )

with tab_submit:
    if current_id == SUBMIT_MATERIAL_ID:
        st.subheader("Submit data")
        st.caption("Fill everything, then click **Submit**.")

        email = st.text_input("Email")
        name = st.text_input("Name")
        title = st.text_input("Title (which data is this?)")
        comments = st.text_area("Comments (optional)", height=100)
        upload = st.file_uploader(
            "File",
            type=["csv", "txt", "pdf", "xlsx", "xls", "json"],
        )

        if st.button("Submit", type="primary", use_container_width=True):
            email_clean = email.strip().lower()
            name_clean = name.strip()
            title_clean = title.strip()
            comments_clean = comments.strip()

            allowed = [
                e.strip().lower()
                for e in st.secrets.get("ALLOWED_EMAILS", [])
            ]
            
            st.write("Thank you!! =>", repr(allowed))
            if not email_clean:
                st.error("Enter your email address.")
            elif email_clean not in allowed:
                st.error("This email is not approved yet. Contact Lumin.")
            elif not name_clean:
                st.error("Enter your name.")
            elif not title_clean:
                st.error("Enter a title.")
            elif upload is None:
                st.error("Attach a file.")
            else:
                st.session_state.last_submission = {
                    "email": email_clean,
                    "name": name_clean,
                    "title": title_clean,
                    "comments": comments_clean,
                    "filename": upload.name,
                    "bytes": upload.getvalue(),
                }
                #
                try:
                    upload_submission_to_repo(
                        filename=upload.name,
                        file_bytes=upload.getvalue(),
                        email=email_clean,
                    )
                    st.success("Submitted and uploaded to GitHub.")
                except Exception as e:
                    st.error(f"GitHub upload failed: {e}")
                st.markdown(
                    f"**Name:** {name_clean}  \n"
                    f"**Email:** {email_clean}  \n"
                    f"**Title:** {title_clean}  \n"
                    f"**File:** {upload.name}"
                )
                if comments_clean:
                    st.markdown(f"**Comments:** {comments_clean}")
    else:
        st.info("Click **Add data** in the sidebar to start.")
