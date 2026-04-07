from neo4j import GraphDatabase
import streamlit as st

uri = st.secrets["NEO4J_URI"]
user = st.secrets["NEO4J_USERNAME"]
password = st.secrets["NEO4J_PASSWORD"]
driver = GraphDatabase.driver(uri, auth=(user, password))

def run_query(q, params=None):
    with driver.session() as session:
        return [r.data() for r in session.run(q, params or {})]

st.title("AIF Graph Viewer 2")




st.set_page_config(page_title="AIF Graph Viewer 2", layout="wide")


uri = st.secrets["NEO4J_URI"]
user = st.secrets["NEO4J_USERNAME"]
password = st.secrets["NEO4J_PASSWORD"]

driver = GraphDatabase.driver(uri, auth=(user, password))


def run_query(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with driver.session() as session:
        result = session.run(query, params or {})
        return [record.data() for record in result]


def sanitize_rel_type(rel_type: str) -> str:
    cleaned = rel_type.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_]+", cleaned):
        raise ValueError(f"Invalid relationship type: {rel_type}")
    return cleaned


def node_to_display(node_props: dict[str, Any], labels: list[str] | None = None) -> str:
    for key in ("material", "name", "value", "code", "id", "title"):
        if key in node_props and node_props[key] not in (None, ""):
            return str(node_props[key])
    if labels:
        return f"<{':'.join(labels)}>"
    return "<node>"


def format_node_row(row: dict[str, Any]) -> dict[str, Any]:
    props = row.get("props", {}) or {}
    labels = row.get("labels", []) or []
    return {
        "element_id": row["element_id"],
        "labels": ", ".join(labels),
        "display": node_to_display(props, labels),
        **props,
    }


def get_materials() -> list[str]:
    queries = [
        """
        MATCH (m:Material)
        WHERE m.material IS NOT NULL
        RETURN DISTINCT m.material AS material
        ORDER BY material
        """,
        """
        MATCH (m:Material)
        WHERE m.name IS NOT NULL
        RETURN DISTINCT m.name AS material
        ORDER BY material
        """,
        """
        MATCH (m:Name)
        WHERE m.name IS NOT NULL
        RETURN DISTINCT m.name AS material
        ORDER BY material
        """,
    ]

    for query in queries:
        rows = run_query(query)
        materials = [row["material"] for row in rows if row.get("material")]
        if materials:
            return materials

    return []


def detect_material_match() -> tuple[str, str]:
    checks = [
        ("Material", "material", "MATCH (m:Material) WHERE m.material IS NOT NULL RETURN count(m) AS c"),
        ("Material", "name", "MATCH (m:Material) WHERE m.name IS NOT NULL RETURN count(m) AS c"),
        ("Name", "name", "MATCH (m:Name) WHERE m.name IS NOT NULL RETURN count(m) AS c"),
    ]

    for label, prop, query in checks:
        rows = run_query(query)
        if rows and rows[0]["c"] > 0:
            return label, prop

    return "Material", "material"


MATERIAL_LABEL, MATERIAL_PROP = detect_material_match()


def get_level1_options(material_name: str) -> list[str]:
    query = f"""
    MATCH (m:`{MATERIAL_LABEL}` {{{MATERIAL_PROP}: $material_name}})-[r]->()
    RETURN DISTINCT type(r) AS rel
    ORDER BY rel
    """
    rows = run_query(query, {"material_name": material_name})
    return [row["rel"] for row in rows if row.get("rel")]


def get_children_from_material(material_name: str, rel_type: str) -> list[dict[str, Any]]:
    rel_type = sanitize_rel_type(rel_type)
    query = f"""
    MATCH (m:`{MATERIAL_LABEL}` {{{MATERIAL_PROP}: $material_name}})-[:`{rel_type}`]->(n)
    RETURN
        elementId(n) AS element_id,
        labels(n) AS labels,
        properties(n) AS props
    ORDER BY coalesce(n.name, n.material, n.value, n.code, n.id)
    """
    rows = run_query(query, {"material_name": material_name})
    return [format_node_row(row) for row in rows]


def get_next_rel_options(parent_ids: list[str]) -> list[str]:
    if not parent_ids:
        return []

    query = """
    MATCH (n)
    WHERE elementId(n) IN $parent_ids
    MATCH (n)-[r]->()
    RETURN DISTINCT type(r) AS rel
    ORDER BY rel
    """
    rows = run_query(query, {"parent_ids": parent_ids})
    return [row["rel"] for row in rows if row.get("rel")]


def get_children_from_nodes(parent_ids: list[str], rel_type: str) -> list[dict[str, Any]]:
    if not parent_ids:
        return []

    rel_type = sanitize_rel_type(rel_type)
    query = f"""
    MATCH (n)-[:`{rel_type}`]->(child)
    WHERE elementId(n) IN $parent_ids
    RETURN DISTINCT
        elementId(child) AS element_id,
        labels(child) AS labels,
        properties(child) AS props
    ORDER BY coalesce(child.name, child.material, child.value, child.code, child.id)
    """
    rows = run_query(query, {"parent_ids": parent_ids})
    return [format_node_row(row) for row in rows]


def dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for node in nodes:
        eid = node["element_id"]
        if eid not in seen:
            seen.add(eid)
            output.append(node)
    return output


def show_node_table(title: str, nodes: list[dict[str, Any]]) -> None:
    st.markdown(f"**{title}**")
    if not nodes:
        st.info("No data")
        return

    df = pd.DataFrame(nodes)
    preferred = ["display", "labels", "element_id"]
    other_cols = [c for c in df.columns if c not in preferred]
    df = df[[c for c in preferred if c in df.columns] + other_cols]
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_branch(
    parent_ids: list[str],
    path: list[str],
    visited_ids: set[str],
    level: int,
) -> None:
    next_options = get_next_rel_options(parent_ids)
    if not next_options:
        return

    widget_key = f"multiselect__{'__'.join(path)}"
    selected_next = st.multiselect(
        f"Level {level} options for {' → '.join(path)}",
        options=next_options,
        key=widget_key,
    )

    for rel in selected_next:
        child_nodes = dedupe_nodes(get_children_from_nodes(parent_ids, rel))
        child_nodes = [n for n in child_nodes if n["element_id"] not in visited_ids]

        if not child_nodes:
            st.info(f"{' → '.join(path + [rel])}: no further nodes")
            continue

        with st.expander(f"{' → '.join(path + [rel])} ({len(child_nodes)} nodes)", expanded=True):
            show_node_table("Nodes", child_nodes)

            next_parent_ids = [node["element_id"] for node in child_nodes]
            next_visited = visited_ids | set(next_parent_ids)
            render_branch(
                parent_ids=next_parent_ids,
                path=path + [rel],
                visited_ids=next_visited,
                level=level + 1,
            )


st.title("AIF Graph Viewer 2")

materials = get_materials()

if not materials:
    st.error(
        "No materials found. Check your graph labels/properties. "
        "Tried: (:Material {material}), (:Material {name}), (:Name {name})."
    )
    st.stop()

selected_material = st.selectbox("Material", materials, key="selected_material")

level1_options = get_level1_options(selected_material)

if not level1_options:
    st.warning("No outgoing relationships found for the selected material.")
    st.stop()

level1_selected = st.multiselect(
    "Level 1",
    options=level1_options,
    key="level1_selected",
)

for rel in level1_selected:
    nodes = dedupe_nodes(get_children_from_material(selected_material, rel))

    with st.container(border=True):
        st.subheader(rel)
        show_node_table(f"{selected_material} → {rel}", nodes)

        parent_ids = [node["element_id"] for node in nodes]
        visited = set(parent_ids)
        render_branch(
            parent_ids=parent_ids,
            path=[rel],
            visited_ids=visited,
            level=2,
        )


st.divider()
st.subheader("Utility Queries")

if st.button("Count Nodes"):
    rows = run_query("MATCH (n) RETURN count(n) AS total")
    st.write(rows[0]["total"] if rows else 0)

string_nodes = run_query(
    """
    MATCH (n:String)
    WHERE n.value IS NOT NULL
    RETURN n.value AS value
    ORDER BY value
    LIMIT 100
    """
)
string_values = [row["value"] for row in string_nodes]

if string_values:
    selected_string = st.selectbox("Select String node", string_values, key="selected_string")

    if st.button("Show Children"):
        rows = run_query(
            """
            MATCH (p:String {value: $name})-[:HAS_CHILD]->(c)
            RETURN c.value AS child
            ORDER BY child
            """,
            {"name": selected_string},
        )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    if st.button("Inspect Node"):
        rows = run_query(
            """
            MATCH (n:String {value: $name})-[r]-(m)
            RETURN
                n.value AS node,
                type(r) AS rel,
                coalesce(m.value, m.name, m.material, toString(id(m))) AS connected
            ORDER BY rel, connected
            """,
            {"name": selected_string},
        )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

if st.button("Show Top-Level Categories"):
    rows = run_query(
        """
        MATCH (n:String)
        WHERE n.value IS NOT NULL
          AND NOT (()-[:HAS_CHILD]->(n))
        RETURN n.value AS category
        ORDER BY category
        """
    )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

if st.button("Show Full Database"):
    rows = run_query(
        """
        MATCH (n:String)
        OPTIONAL MATCH (n)-[:HAS_CHILD]->(c:String)
        RETURN n.value AS parent, collect(c.value) AS children
        ORDER BY parent
        """
    )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

if st.button("Show Full Hierarchy"):
    rows = run_query(
        """
        MATCH (n:String)
        OPTIONAL MATCH (n)-[:HAS_CHILD]->(c:String)
        RETURN n.value AS parent, collect(c.value) AS children
        ORDER BY parent
        """
    )

    tree = {row["parent"]: [c for c in row["children"] if c] for row in rows}

    root_rows = run_query(
        """
        MATCH (n:String)
        WHERE n.value IS NOT NULL
          AND NOT (()-[:HAS_CHILD]->(n))
        RETURN n.value AS root
        ORDER BY root
        """
    )
    roots = [row["root"] for row in root_rows]

    def show_tree(node: str, level: int = 0, seen: set[str] | None = None) -> None:
        seen = seen or set()
        if node in seen:
            st.markdown(f"{'&nbsp;' * 4 * level}• {node} *(cycle detected)*", unsafe_allow_html=True)
            return

        st.markdown(f"{'&nbsp;' * 4 * level}• {node}", unsafe_allow_html=True)

        for child in tree.get(node, []):
            show_tree(child, level + 1, seen | {node})

    for root in roots:
        show_tree(root)
# --- BUTTON 1: count nodes ---
if st.button("Count Nodes"):
    res = run_query("MATCH (n:String) RETURN count(n) AS total")
    st.write(res)

# --- BUTTON 2: select node ---
nodes = [r["value"] for r in run_query(
    "MATCH (n:String) RETURN n.value AS value ORDER BY value LIMIT 100"
)]

selected = st.selectbox("Select node", nodes)

# --- BUTTON 3: show children ---
if st.button("Show Children"):
    res = run_query("""
    MATCH (p:String {value:$name})-[:HAS_CHILD]->(c)
    RETURN c.value AS child
    """, {"name": selected})
    st.write(res)

# --- BUTTON 4: show connections ---
if st.button("Inspect Node"):
    res = run_query("""
    MATCH (n:String {value:$name})-[r]-(m)
    RETURN n.value AS node, type(r) AS rel, m.value AS connected
    """, {"name": selected})
    st.write(res)
    
if st.button("Show Top-Level Categories"):
    res = run_query("""
    MATCH (n:String)
    WHERE NOT ( ()-[:HAS_CHILD]->(n) )
    RETURN n.value AS category
    ORDER BY category
    """)
    st.write(res)
    
if st.button("Load Top-Level Categories"):
    st.session_state["roots"] = [
        r["category"] for r in run_query("""
        MATCH (n:String)
        WHERE NOT ( ()-[:HAS_CHILD]->(n) )
        RETURN n.value AS category
        ORDER BY category
        """)
    ]

# --- BUTTON: show full database ---
if st.button("Show Full Database"):
    data = run_query("""
    MATCH (n:String)
    OPTIONAL MATCH (n)-[:HAS_CHILD]->(c:String)
    RETURN n.value AS parent, collect(c.value) AS children
    ORDER BY parent
    """)

    st.subheader("Full Graph (Table)")
    st.dataframe(data)

    # --- build tree ---
    tree = {row["parent"]: row["children"] for row in data}

    # --- find roots ---
    roots = run_query("""
    MATCH (n:String)
    WHERE NOT ( ()-[:HAS_CHILD]->(n) )
    RETURN n.value AS root
    """)
    roots = [r["root"] for r in roots]

    # --- recursive display ---
    def show_tree(node, level=0):
        st.write("  " * level + "• " + node)
        for child in tree.get(node, []):
            if child:
                show_tree(child, level + 1)

    st.subheader("Hierarchy View")

    for r in roots:
        show_tree(r)
        
# --- BUTTON: show hierarchy ---
if st.button("Show Full Hierarchy"):

    # get parent → children
    data = run_query("""
    MATCH (n:String)
    OPTIONAL MATCH (n)-[:HAS_CHILD]->(c:String)
    RETURN n.value AS parent, collect(c.value) AS children
    ORDER BY parent
    """)

    tree = {row["parent"]: row["children"] for row in data}

    # find roots
    roots = run_query("""
    MATCH (n:String)
    WHERE NOT ( ()-[:HAS_CHILD]->(n) )
    RETURN n.value AS root
    """)
    roots = [r["root"] for r in roots]

    # recursive tree display
    def show_tree(node, level=0):
        st.markdown(f"{'&nbsp;&nbsp;&nbsp;' * level}• {node}", unsafe_allow_html=True)
        for child in tree.get(node, []):
            if child:
                show_tree(child, level + 1)

    st.subheader("Hierarchy")

    for r in roots:
        show_tree(r)
