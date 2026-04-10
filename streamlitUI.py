import streamlit as st
from neo4j import GraphDatabase


uri = st.secrets["NEO4J_URI"]
user = st.secrets["NEO4J_USERNAME"]
password = st.secrets["NEO4J_PASSWORD"]

driver = GraphDatabase.driver(uri, auth=(user, password))

st.set_page_config(page_title="AIF Graph Viewer 2", layout="wide")
st.title("AIF Graph Viewer 2")


def run_query(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with driver.session() as session:
        return [record.data() for record in session.run(query, params or {})]


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text


def normalize_nodes(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for row in rows:
        node_id = row.get("id")
        value = clean_text(row.get("value"))
        if node_id and value:
            items.append({"id": str(node_id), "value": value})
    return items


def get_root_nodes() -> list[dict[str, str]]:
    rows = run_query(
        """
        MATCH (n:String)
        WHERE NOT ()-[:HAS_CHILD]->(n)
          AND n.value IS NOT NULL
          AND trim(n.value) <> ""
          AND toLower(trim(n.value)) <> "null"
        RETURN elementId(n) AS id, n.value AS value
        ORDER BY n.value, elementId(n)
        """
    )
    return normalize_nodes(rows)


def get_children(parent_id: str) -> list[dict[str, str]]:
    rows = run_query(
        """
        MATCH (p:String)-[:HAS_CHILD]->(c:String)
        WHERE elementId(p) = $parent_id
          AND c.value IS NOT NULL
          AND trim(c.value) <> ""
          AND toLower(trim(c.value)) <> "null"
        RETURN elementId(c) AS id, c.value AS value
        ORDER BY c.value, elementId(c)
        """,
        {"parent_id": parent_id},
    )
    return normalize_nodes(rows)


def get_descendants(node_id: str) -> list[dict[str, str]]:
    rows = run_query(
        """
        MATCH (n:String)-[:HAS_CHILD*1..12]->(m:String)
        WHERE elementId(n) = $node_id
          AND m.value IS NOT NULL
          AND trim(m.value) <> ""
          AND toLower(trim(m.value)) <> "null"
        RETURN DISTINCT
            elementId(m) AS id,
            m.value AS value,
            coalesce(m.kind, "") AS kind
        ORDER BY kind, value, id
        """,
        {"node_id": node_id},
    )

    items: list[dict[str, str]] = []
    for row in rows:
        node_id_value = row.get("id")
        value = clean_text(row.get("value"))
        if node_id_value and value:
            items.append(
                {
                    "id": str(node_id_value),
                    "value": value,
                    "kind": str(row.get("kind", "")),
                }
            )
    return items


def get_direct_children_for_inspect(node_id: str) -> list[dict[str, str]]:
    rows = run_query(
        """
        MATCH (p:String)-[:HAS_CHILD]->(c:String)
        WHERE elementId(p) = $node_id
          AND c.value IS NOT NULL
          AND trim(c.value) <> ""
          AND toLower(trim(c.value)) <> "null"
        RETURN
            elementId(c) AS id,
            c.value AS child,
            coalesce(c.kind, "") AS kind
        ORDER BY kind, child, id
        """,
        {"node_id": node_id},
    )

    items: list[dict[str, str]] = []
    for row in rows:
        child_id = row.get("id")
        child_value = clean_text(row.get("child"))
        if child_id and child_value:
            items.append(
                {
                    "id": str(child_id),
                    "child": child_value,
                    "kind": str(row.get("kind", "")),
                }
            )
    return items


def inspect_node_connections(node_id: str) -> list[dict[str, str]]:
    rows = run_query(
        """
        MATCH (n:String)-[r]-(m:String)
        WHERE elementId(n) = $node_id
          AND m.value IS NOT NULL
          AND trim(m.value) <> ""
          AND toLower(trim(m.value)) <> "null"
        RETURN DISTINCT
            elementId(n) AS node_id,
            n.value AS node,
            type(r) AS rel,
            elementId(m) AS connected_id,
            m.value AS connected,
            coalesce(m.kind, "") AS connected_kind
        ORDER BY rel, connected, connected_id
        """,
        {"node_id": node_id},
    )

    items: list[dict[str, str]] = []
    for row in rows:
        node_value = clean_text(row.get("node"))
        connected_value = clean_text(row.get("connected"))
        if node_value and connected_value:
            items.append(
                {
                    "node_id": str(row["node_id"]),
                    "node": node_value,
                    "rel": str(row["rel"]),
                    "connected_id": str(row["connected_id"]),
                    "connected": connected_value,
                    "connected_kind": str(row.get("connected_kind", "")),
                }
            )
    return items


def get_top_level_categories() -> list[dict[str, str]]:
    rows = run_query(
        """
        MATCH (n:String)
        WHERE NOT ()-[:HAS_CHILD]->(n)
          AND n.value IS NOT NULL
          AND trim(n.value) <> ""
          AND toLower(trim(n.value)) <> "null"
        RETURN
            elementId(n) AS id,
            n.value AS category,
            coalesce(n.kind, "") AS kind
        ORDER BY kind, category, id
        """
    )

    items: list[dict[str, str]] = []
    for row in rows:
        category = clean_text(row.get("category"))
        node_id = row.get("id")
        if node_id and category:
            items.append(
                {
                    "id": str(node_id),
                    "category": category,
                    "kind": str(row.get("kind", "")),
                }
            )
    return items


def get_node_count() -> int:
    rows = run_query("MATCH (n:String) RETURN count(n) AS total")
    return int(rows[0]["total"]) if rows else 0


def get_full_graph() -> list[dict[str, Any]]:
    return run_query(
        """
        MATCH (n:String)
        OPTIONAL MATCH (n)-[:HAS_CHILD]->(c:String)
        RETURN
            elementId(n) AS parent_id,
            n.value AS parent,
            coalesce(n.kind, "") AS parent_kind,
            collect({
                id: elementId(c),
                value: c.value,
                kind: coalesce(c.kind, "")
            }) AS children
        ORDER BY parent_kind, parent, parent_id
        """
    )


def build_tree_map(data: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    tree: dict[str, dict[str, Any]] = {}
    for row in data:
        parent_id = row.get("parent_id")
        parent_value = clean_text(row.get("parent"))
        if not parent_id or not parent_value:
            continue

        valid_children: list[dict[str, str]] = []
        for child in row.get("children", []):
            if not child:
                continue
            child_id = child.get("id")
            child_value = clean_text(child.get("value"))
            if child_id and child_value:
                valid_children.append(
                    {
                        "id": str(child_id),
                        "value": child_value,
                        "kind": str(child.get("kind", "")),
                    }
                )

        tree[str(parent_id)] = {
            "parent_id": str(parent_id),
            "parent": parent_value,
            "parent_kind": str(row.get("parent_kind", "")),
            "children": valid_children,
        }
    return tree


def render_dropdown(level: int, nodes: list[dict[str, str]]) -> str | None:
    option_ids = [node["id"] for node in nodes]
    labels = {node["id"]: node["value"] for node in nodes}

    saved_id = (
        st.session_state.path_ids[level]
        if level < len(st.session_state.path_ids)
        else None
    )

    if saved_id not in option_ids:
        saved_id = None

    index = option_ids.index(saved_id) if saved_id is not None else None

    return st.selectbox(
        f"Level {level + 1}",
        options=option_ids,
        index=index,
        placeholder="Select",
        key=f"path_{level}",
        format_func=lambda node_id: labels[node_id],
    )


def render_tree(node_id: str, tree: dict[str, dict[str, Any]], level: int = 0) -> None:
    node = tree.get(node_id)
    if not node:
        return

    label = node["parent"]
    kind = node.get("parent_kind", "")
    shown = f"{label} [{kind}]" if kind else label

    st.markdown(f"{'&nbsp;&nbsp;&nbsp;' * level}• {shown}", unsafe_allow_html=True)

    for child in node.get("children", []):
        render_tree(child["id"], tree, level + 1)


if "path_ids" not in st.session_state:
    st.session_state.path_ids = []

if "roots" not in st.session_state:
    st.session_state.roots = []

st.subheader("Browse Hierarchy")

level = 0
nodes = get_root_nodes()

while True:
    if not nodes:
        st.session_state.path_ids = st.session_state.path_ids[:level]
        break

    selected_id = render_dropdown(level, nodes)

    if selected_id is None:
        st.session_state.path_ids = st.session_state.path_ids[:level]
        break

    if len(st.session_state.path_ids) > level:
        st.session_state.path_ids[level] = selected_id
    else:
        st.session_state.path_ids.append(selected_id)

    st.session_state.path_ids = st.session_state.path_ids[: level + 1]
    nodes = get_children(selected_id)
    level += 1

selected_path_id = st.session_state.path_ids[-1] if st.session_state.path_ids else None

st.subheader("Inspect Current Branch")

inspect_nodes = get_descendants(selected_path_id) if selected_path_id else []
inspect_option_ids = [node["id"] for node in inspect_nodes]
inspect_labels = {
    node["id"]: f'{node["value"]} [{node["kind"]}]' if node.get("kind") else node["value"]
    for node in inspect_nodes
}

selected_inspect_id = st.selectbox(
    "Select node",
    options=inspect_option_ids,
    index=None,
    placeholder="Select",
    key="inspect_selectbox",
    format_func=lambda node_id: inspect_labels[node_id],
)

col1, col2, col3, col4 = st.columns(4)

with col1:
    if st.button("Count Nodes", key="count_nodes_button"):
        st.write([{"total": get_node_count()}])

with col2:
    if st.button("Show Children", key="show_children_button"):
        if selected_inspect_id is None:
            st.write("Select a node from the current branch.")
        else:
            st.write(get_direct_children_for_inspect(selected_inspect_id))

with col3:
    if st.button("Inspect Node", key="inspect_node_button"):
        if selected_inspect_id is None:
            st.write("Select a node from the current branch.")
        else:
            st.write(inspect_node_connections(selected_inspect_id))

with col4:
    if st.button("Show Top-Level Categories", key="show_top_level_categories_button"):
        st.write(get_top_level_categories())

if st.button("Load Top-Level Categories", key="load_top_level_categories_button"):
    st.session_state.roots = [item["category"] for item in get_top_level_categories()]
    st.write(st.session_state.roots)

if st.button("Show Full Database", key="show_full_database_button"):
    data = get_full_graph()
    st.subheader("Full Graph (Table)")
    st.dataframe(data, use_container_width=True)

if st.button("Show Full Hierarchy", key="show_full_hierarchy_button"):
    data = get_full_graph()
    tree = build_tree_map(data)
    roots = get_top_level_categories()

    st.subheader("Hierarchy")
    for root in roots:
        render_tree(root["id"], tree)
