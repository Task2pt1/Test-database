# app.py

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

import pandas as pd
import streamlit as st
from neo4j import GraphDatabase, Driver


st.set_page_config(page_title="Material Ontology Explorer", layout="wide")


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
NODE_LABEL = "String"
NODE_DISPLAY_PROPERTIES = ("value", "name", "label")
CHILD_REL = "HAS_CHILD"
MEASUREMENT_REL = "HAS_MEASUREMENT"
MEASUREMENT_LABEL = "Measurement"


# -----------------------------------------------------------------------------
# Neo4j connection
# -----------------------------------------------------------------------------
@st.cache_resource
def get_driver() -> Driver:
    uri = st.secrets["NEO4J_URI"]
    user = st.secrets["NEO4J_USERNAME"]
    password = st.secrets["NEO4J_PASSWORD"]
    return GraphDatabase.driver(uri, auth=(user, password))


driver = get_driver()


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def run_query(query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with driver.session() as session:
        result = session.run(query, params or {})
        return [record.data() for record in result]


def display_name_from_props(props: dict[str, Any]) -> str:
    for key in NODE_DISPLAY_PROPERTIES:
        value = props.get(key)
        if value not in (None, ""):
            return str(value)
    return "(unnamed)"


def safe_join_path(path_values: list[str]) -> str:
    cleaned = [str(v) for v in path_values if v not in (None, "")]
    return " → ".join(cleaned)


def flatten_dict(prefix: str, payload: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in payload.items():
        flat[f"{prefix}.{key}"] = value
    return flat


def to_excel_bytes(
    selected_nodes_df: pd.DataFrame,
    selected_measurements_df: pd.DataFrame,
    full_subtree_df: pd.DataFrame,
) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        selected_nodes_df.to_excel(writer, sheet_name="selected_nodes", index=False)
        selected_measurements_df.to_excel(writer, sheet_name="selected_measurements", index=False)
        full_subtree_df.to_excel(writer, sheet_name="full_subtree", index=False)
    output.seek(0)
    return output.read()


# -----------------------------------------------------------------------------
# Cypher queries
# -----------------------------------------------------------------------------
def get_root_nodes() -> list[dict[str, str]]:
    query = f"""
    MATCH (n:{NODE_LABEL})
    WHERE NOT EXISTS {{
        MATCH (:{NODE_LABEL})-[:{CHILD_REL}]->(n)
    }}
    RETURN elementId(n) AS id,
           coalesce(n.value, n.name, n.label, elementId(n)) AS label
    ORDER BY label
    """
    return run_query(query)


def get_node_summary(node_id: str) -> dict[str, Any] | None:
    query = f"""
    MATCH (n)
    WHERE elementId(n) = $node_id
    RETURN elementId(n) AS id,
           labels(n) AS labels,
           properties(n) AS props,
           coalesce(n.value, n.name, n.label, elementId(n)) AS label,
           EXISTS {{
               MATCH (n)-[:{CHILD_REL}]->(:{NODE_LABEL})
           }} AS has_children,
           EXISTS {{
               MATCH (n)-[:{MEASUREMENT_REL}]->()
           }} AS has_measurements
    """
    rows = run_query(query, {"node_id": node_id})
    return rows[0] if rows else None


def get_children(node_id: str) -> list[dict[str, str]]:
    query = f"""
    MATCH (n)-[:{CHILD_REL}]->(c:{NODE_LABEL})
    WHERE elementId(n) = $node_id
    RETURN elementId(c) AS id,
           coalesce(c.value, c.name, c.label, elementId(c)) AS label
    ORDER BY label
    """
    return run_query(query, {"node_id": node_id})


def get_breadcrumb_labels(path_ids: list[str]) -> list[str]:
    if not path_ids:
        return []

    query = """
    UNWIND $path_ids AS node_id
    MATCH (n)
    WHERE elementId(n) = node_id
    RETURN node_id,
           coalesce(n.value, n.name, n.label, elementId(n)) AS label
    """
    rows = run_query(query, {"path_ids": path_ids})
    label_map = {row["node_id"]: row["label"] for row in rows}
    return [label_map.get(node_id, node_id) for node_id in path_ids]


def get_subtree_with_measurements(root_id: str) -> list[dict[str, Any]]:
    query = f"""
    MATCH p = (root)-[:{CHILD_REL}*0..]->(n)
    WHERE elementId(root) = $root_id
    WITH n, p, length(p) AS depth,
         [x IN nodes(p) | coalesce(x.value, x.name, x.label, elementId(x))] AS path_labels
    OPTIONAL MATCH (n)-[:{MEASUREMENT_REL}]->(m)
    RETURN
        elementId(n) AS node_id,
        coalesce(n.value, n.name, n.label, elementId(n)) AS node_label,
        labels(n) AS node_labels,
        properties(n) AS node_props,
        depth,
        path_labels,
        collect(
            DISTINCT CASE
                WHEN m IS NULL THEN NULL
                ELSE {{
                    measurement_id: elementId(m),
                    measurement_labels: labels(m),
                    measurement_props: properties(m)
                }}
            END
        ) AS measurements
    ORDER BY depth, node_label
    """
    return run_query(query, {"root_id": root_id})


def get_direct_measurements(node_id: str) -> list[dict[str, Any]]:
    query = f"""
    MATCH (n)-[:{MEASUREMENT_REL}]->(m)
    WHERE elementId(n) = $node_id
    RETURN elementId(m) AS measurement_id,
           labels(m) AS measurement_labels,
           properties(m) AS measurement_props
    ORDER BY elementId(m)
    """
    return run_query(query, {"node_id": node_id})


# -----------------------------------------------------------------------------
# Transform query results into exportable DataFrames
# -----------------------------------------------------------------------------
@dataclass
class ExportFrames:
    subtree_df: pd.DataFrame
    node_rows_df: pd.DataFrame
    measurement_rows_df: pd.DataFrame


def build_export_frames(subtree_rows: list[dict[str, Any]]) -> ExportFrames:
    node_rows: list[dict[str, Any]] = []
    measurement_rows: list[dict[str, Any]] = []

    for row in subtree_rows:
        node_id = row["node_id"]
        node_label = row["node_label"]
        node_labels = row["node_labels"]
        node_props = row["node_props"] or {}
        depth = row["depth"]
        path_labels = row["path_labels"] or []
        measurements = [m for m in (row["measurements"] or []) if m is not None]

        node_base = {
            "selected": True,
            "node_id": node_id,
            "node_label": node_label,
            "node_labels": ", ".join(node_labels),
            "depth": depth,
            "path": safe_join_path(path_labels),
            "measurement_count": len(measurements),
        }
        node_base.update(flatten_dict("node", node_props))
        node_rows.append(node_base)

        if measurements:
            for measurement in measurements:
                measurement_props = measurement["measurement_props"] or {}
                measurement_row = {
                    "selected": True,
                    "node_id": node_id,
                    "node_label": node_label,
                    "path": safe_join_path(path_labels),
                    "measurement_id": measurement["measurement_id"],
                    "measurement_labels": ", ".join(measurement["measurement_labels"] or []),
                }
                measurement_row.update(flatten_dict("measurement", measurement_props))
                measurement_rows.append(measurement_row)
        else:
            measurement_rows.append(
                {
                    "selected": False,
                    "node_id": node_id,
                    "node_label": node_label,
                    "path": safe_join_path(path_labels),
                    "measurement_id": None,
                    "measurement_labels": None,
                }
            )

    subtree_df = pd.DataFrame(node_rows)
    node_rows_df = pd.DataFrame(node_rows)
    measurement_rows_df = pd.DataFrame(measurement_rows)

    if not subtree_df.empty:
        subtree_df = subtree_df.sort_values(by=["depth", "path", "node_label"], kind="stable").reset_index(drop=True)
    if not node_rows_df.empty:
        node_rows_df = node_rows_df.sort_values(by=["depth", "path", "node_label"], kind="stable").reset_index(drop=True)
    if not measurement_rows_df.empty:
        measurement_rows_df = measurement_rows_df.sort_values(by=["path", "node_label"], kind="stable").reset_index(drop=True)

    return ExportFrames(
        subtree_df=subtree_df,
        node_rows_df=node_rows_df,
        measurement_rows_df=measurement_rows_df,
    )


# -----------------------------------------------------------------------------
# Session state
# -----------------------------------------------------------------------------
if "path_ids" not in st.session_state:
    st.session_state.path_ids = []

if "selected_export_root" not in st.session_state:
    st.session_state.selected_export_root = None


# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------
st.title("Material Ontology Explorer")
st.caption("Browse ontology in the sidebar, review descendants in the main panel, and export selected nodes and measurements to Excel.")

with st.sidebar:
    st.header("Ontology navigation")

    root_nodes = get_root_nodes()
    if not root_nodes:
        st.error("No root ontology nodes were found.")
        st.stop()

    root_options = [row["id"] for row in root_nodes]
    root_label_map = {row["id"]: row["label"] for row in root_nodes}

    root_placeholder = "— select root ontology —"
    root_selected = st.selectbox(
        "Root ontology",
        options=[None] + root_options,
        index=0 if not st.session_state.path_ids else [None] + root_options.index(st.session_state.path_ids[0]) + 1,  # type: ignore[operator]
        format_func=lambda x: root_placeholder if x is None else root_label_map[x],
        key="root_selectbox",
    )

    if root_selected is None:
        st.session_state.path_ids = []
    else:
        if not st.session_state.path_ids or st.session_state.path_ids[0] != root_selected:
            st.session_state.path_ids = [root_selected]

        level = 1
        while True:
            parent_id = st.session_state.path_ids[level - 1]
            children = get_children(parent_id)
            if not children:
                break

            child_ids = [child["id"] for child in children]
            child_label_map = {child["id"]: child["label"] for child in children}

            current_value = st.session_state.path_ids[level] if level < len(st.session_state.path_ids) else None

            chosen = st.selectbox(
                f"Level {level + 1}",
                options=[None] + child_ids,
                index=([None] + child_ids).index(current_value) if current_value in child_ids else 0,
                format_func=lambda x: "— stop here —" if x is None else child_label_map[x],
                key=f"ontology_level_{level}",
            )

            if chosen is None:
                st.session_state.path_ids = st.session_state.path_ids[:level]
                break

            if level < len(st.session_state.path_ids):
                if st.session_state.path_ids[level] != chosen:
                    st.session_state.path_ids = st.session_state.path_ids[:level] + [chosen]
            else:
                st.session_state.path_ids.append(chosen)

            level += 1

        if st.button("Reset path", use_container_width=True):
            st.session_state.path_ids = [root_selected]
            st.rerun()

    st.divider()

    if st.session_state.path_ids:
        breadcrumb = get_breadcrumb_labels(st.session_state.path_ids)
        st.markdown("**Current path**")
        st.caption(safe_join_path(breadcrumb))


if not st.session_state.path_ids:
    st.info("Select a root ontology in the sidebar to begin.")
    st.stop()


current_node_id = st.session_state.path_ids[-1]
current_node = get_node_summary(current_node_id)

if current_node is None:
    st.error("Selected node could not be loaded.")
    st.stop()


col1, col2 = st.columns([1.2, 2.0])

with col1:
    st.subheader("Current node")
    st.write(f"**Label:** {current_node['label']}")
    st.write(f"**Node ID:** `{current_node['id']}`")
    st.write(f"**Node labels:** {', '.join(current_node['labels']) if current_node['labels'] else '(none)'}")
    st.write(f"**Has children:** {'Yes' if current_node['has_children'] else 'No'}")
    st.write(f"**Has measurements:** {'Yes' if current_node['has_measurements'] else 'No'}")

    with st.expander("Node properties", expanded=False):
        props_df = pd.DataFrame(
            [{"property": key, "value": value} for key, value in (current_node["props"] or {}).items()]
        )
        st.dataframe(props_df, use_container_width=True, hide_index=True)

    with st.expander("Direct measurements on this node", expanded=False):
        direct_measurements = get_direct_measurements(current_node_id)
        if direct_measurements:
            measurement_preview = []
            for row in direct_measurements:
                flat = {
                    "measurement_id": row["measurement_id"],
                    "measurement_labels": ", ".join(row["measurement_labels"] or []),
                }
                flat.update(flatten_dict("measurement", row["measurement_props"] or {}))
                measurement_preview.append(flat)
            st.dataframe(pd.DataFrame(measurement_preview), use_container_width=True, hide_index=True)
        else:
            st.caption("No direct measurements found.")

with col2:
    st.subheader("Subtree export scope")

    subtree_root_mode = st.radio(
        "Export from",
        options=["Current node", "Top-level root"],
        horizontal=True,
        index=0,
    )

    export_root_id = current_node_id if subtree_root_mode == "Current node" else st.session_state.path_ids[0]
    export_root_summary = get_node_summary(export_root_id)
    st.session_state.selected_export_root = export_root_id

    if export_root_summary:
        st.caption(f"Export root: {export_root_summary['label']}")

    subtree_rows = get_subtree_with_measurements(export_root_id)
    export_frames = build_export_frames(subtree_rows)

    if export_frames.subtree_df.empty:
        st.warning("No subtree rows were found for this selection.")
        st.stop()

    st.write(
        f"Loaded **{len(export_frames.node_rows_df)}** node rows and "
        f"**{len(export_frames.measurement_rows_df[export_frames.measurement_rows_df['measurement_id'].notna()])}** measurement rows."
    )

    st.markdown("**Select nodes to export**")
    edited_nodes = st.data_editor(
        export_frames.node_rows_df,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        column_config={
            "selected": st.column_config.CheckboxColumn("Export", help="Include this node in Excel"),
            "node_id": st.column_config.TextColumn("Node ID", disabled=True),
            "node_label": st.column_config.TextColumn("Node", disabled=True),
            "node_labels": st.column_config.TextColumn("Labels", disabled=True),
            "depth": st.column_config.NumberColumn("Depth", disabled=True),
            "path": st.column_config.TextColumn("Path", disabled=True),
            "measurement_count": st.column_config.NumberColumn("Measurements", disabled=True),
        },
        key="node_selection_editor",
    )

    selected_node_ids = set(edited_nodes.loc[edited_nodes["selected"], "node_id"].astype(str).tolist())

    measurement_df = export_frames.measurement_rows_df.copy()
    if not measurement_df.empty:
        measurement_df["selected"] = measurement_df["node_id"].astype(str).isin(selected_node_ids)

    st.markdown("**Measurements that will be exported**")
    st.dataframe(
        measurement_df[measurement_df["selected"]].reset_index(drop=True),
        use_container_width=True,
        hide_index=True,
    )

    selected_nodes_df = edited_nodes[edited_nodes["selected"]].reset_index(drop=True)
    selected_measurements_df = measurement_df[measurement_df["selected"]].reset_index(drop=True)

    excel_bytes = to_excel_bytes(
        selected_nodes_df=selected_nodes_df,
        selected_measurements_df=selected_measurements_df,
        full_subtree_df=export_frames.subtree_df,
    )

    file_stem = export_root_summary["label"] if export_root_summary else "ontology_export"
    sanitized_file_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in file_stem)

    st.download_button(
        label="Download Excel export",
        data=excel_bytes,
        file_name=f"{sanitized_file_stem}_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
