#!/usr/bin/env python3
"""Generate thesis-grade ERD diagrams for the EGX trading-system PostgreSQL schema.

Two modes:
  * ``--thesis`` (default) — 6 core tables only (audit, backtest, social archive, users).
    Excludes ChromaDB substitute (agent_memories), Portfolio Assistant (pa_*), and unused
    stubs (ohlcv_prices, cache_entries).
  * ``--full`` — legacy diagram including all db_schema.sql tables + pa_* cluster.

Crow's-foot notation, hand-laid-out orthogonal routing so connector lines do not cross.
Output: docs/erd/egx_thesis_erd.svg (+ .png when cairosvg or rsvg-convert is available)
        docs/erd/egx_database_erd.svg in --full mode.

Physical table names match db_schema.sql exactly (``social_v2_posts`` kept as-is).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

# ----------------------------------------------------------------------------- geometry
W = 268
ROW_H = 19
TITLE_H = 27

FONT = "Segoe UI, Helvetica, Arial, sans-serif"
MONO = "Consolas, 'DejaVu Sans Mono', monospace"

INK = "#000000"
HEADER_FILL = "#d9d9d9"
ROW_FILL = "#ffffff"
ALT_FILL = "#f4f4f4"
PK_FILL = "#ececec"


@dataclass
class Table:
    name: str
    cols: list  # (col_name, type, key)  key in {'PK','FK','UQ',''}
    x: float = 0
    y: float = 0
    subtitle: str = ""

    @property
    def h(self) -> float:
        return TITLE_H + ROW_H * len(self.cols)

    @property
    def cx(self) -> float:
        return self.x + W / 2

    def row_y(self, col_name: str) -> float:
        for i, (c, _t, _k) in enumerate(self.cols):
            if c == col_name:
                return self.y + TITLE_H + i * ROW_H + ROW_H / 2
        raise KeyError(col_name)


def _thesis_tables() -> dict[str, Table]:
    return {
        "users": Table("users", [
            ("firebase_uid", "text", "PK"),
            ("email", "text", ""),
            ("display_name", "text", ""),
            ("photo_url", "text", ""),
            ("created_at", "timestamptz", ""),
            ("last_seen_at", "timestamptz", ""),
        ]),
        "analysis_sessions": Table("analysis_sessions", [
            ("id", "serial", "PK"),
            ("session_id", "text", "UQ"),
            ("ticker", "text", ""),
            ("trade_date", "date", ""),
            ("market", "text", ""),
            ("run_type", "text", ""),
            ("final_decision", "text", ""),
            ("risk_veto", "bool", ""),
            ("confidence_overall", "numeric", ""),
            ("confidence_scores", "jsonb", ""),
            ("execution_plan", "jsonb", ""),
            ("risk_assessment", "jsonb", ""),
            ("data_quality", "jsonb", ""),
            ("full_state", "jsonb", ""),
            ("user_id", "text", "FK"),
            ("model_fingerprint", "jsonb", ""),
            ("created_at", "timestamptz", ""),
        ]),
        "agent_events": Table("agent_events", [
            ("id", "serial", "PK"),
            ("session_id", "text", "FK"),
            ("event_type", "text", ""),
            ("agent_name", "text", ""),
            ("opinion_type", "text", ""),
            ("opinion_summary", "text", ""),
            ("confidence_score", "numeric", ""),
            ("structured_output", "jsonb", ""),
            ("model_fingerprint", "jsonb", ""),
            ("logged_at", "timestamptz", ""),
        ]),
        "backtest_runs": Table("backtest_runs", [
            ("id", "serial", "PK"),
            ("run_id", "text", "UQ"),
            ("ticker", "text", ""),
            ("strategy", "text", ""),
            ("start_date", "date", ""),
            ("end_date", "date", ""),
            ("total_return_pct", "numeric", ""),
            ("benchmark_return_pct", "numeric", ""),
            ("alpha_pct", "numeric", ""),
            ("sharpe_ratio", "numeric", ""),
            ("calmar_ratio", "numeric", ""),
            ("max_drawdown_pct", "numeric", ""),
            ("win_rate_pct", "numeric", ""),
            ("total_trades", "int", ""),
            ("total_commissions", "numeric", ""),
            ("final_portfolio_egp", "numeric", ""),
            ("metrics", "jsonb", ""),
            ("user_id", "text", "FK"),
            ("created_at", "timestamptz", ""),
        ]),
        "backtest_trades": Table("backtest_trades", [
            ("id", "serial", "PK"),
            ("run_id", "text", "FK"),
            ("trade_date", "date", ""),
            ("action", "text", ""),
            ("shares", "numeric", ""),
            ("price_egp", "numeric", ""),
            ("value_egp", "numeric", ""),
            ("commission_egp", "numeric", ""),
            ("portfolio_value", "numeric", ""),
            ("signal", "text", ""),
            ("confidence", "numeric", ""),
            ("notes", "text", ""),
        ]),
        "social_v2_posts": Table(
            "social_v2_posts",
            [
                ("id", "bigserial", "PK"),
                ("post_hash", "text", "UQ"),
                ("platform", "text", ""),
                ("source", "text", ""),
                ("url", "text", ""),
                ("username", "text", ""),
                ("post_timestamp", "timestamptz", ""),
                ("scraped_at", "timestamptz", ""),
                ("text", "text", ""),
                ("engagement", "int", ""),
                ("symbols", "text[]", ""),
                ("intents", "text[]", ""),
                ("content_label", "text", ""),
                ("sentiment_score", "real", ""),
                ("sentiment_label", "text", ""),
                ("sectors", "text[]", ""),
                ("indices", "text[]", ""),
            ],
            subtitle="(social archive — standalone)",
        ),
    }


def _full_tables() -> dict[str, Table]:
    """Legacy full schema including optional / subsystem tables."""
    t = _thesis_tables()
    t.update({
        "agent_memories": Table("agent_memories", [
            ("id", "int", "PK"), ("agent_name", "text", ""), ("ticker", "text", ""),
            ("situation", "text", ""), ("recommendation", "text", ""),
            ("embedding", "jsonb", ""), ("created_at", "timestamptz", ""),
        ]),
        "ohlcv_prices": Table("ohlcv_prices", [
            ("ticker", "text", "PK"), ("trade_date", "date", "PK"), ("open", "numeric", ""),
            ("high", "numeric", ""), ("low", "numeric", ""), ("close", "numeric", ""),
            ("volume", "bigint", ""), ("source", "text", ""), ("fetched_at", "timestamptz", ""),
        ]),
        "cache_entries": Table("cache_entries", [
            ("cache_key", "text", "PK"), ("value", "jsonb", ""), ("data_type", "text", ""),
            ("expires_at", "timestamptz", ""), ("created_at", "timestamptz", ""),
        ]),
        "pa_conversations": Table("pa_conversations", [
            ("id", "uuid", "PK"), ("user_id", "text", ""), ("title", "text", ""),
            ("language", "text", ""), ("active_ref", "text", ""),
            ("last_proposal_id", "bigint", ""), ("archived", "bool", ""),
            ("created_at", "timestamptz", ""), ("updated_at", "timestamptz", ""),
        ]),
        "pa_messages": Table("pa_messages", [
            ("id", "bigint", "PK"), ("conversation_id", "uuid", "FK"), ("role", "text", ""),
            ("text_content", "text", ""), ("blocks", "jsonb", ""),
            ("scenario_id", "bigint", ""), ("created_at", "timestamptz", ""),
        ]),
        "pa_events": Table("pa_events", [
            ("id", "bigint", "PK"), ("conversation_id", "uuid", "FK"),
            ("message_id", "bigint", ""), ("event_type", "text", ""), ("stage", "text", ""),
            ("payload", "jsonb", ""), ("logged_at", "timestamptz", ""),
        ]),
        "pa_policies": Table("pa_policies", [
            ("id", "bigint", "PK"), ("conversation_id", "uuid", "FK"), ("version", "int", ""),
            ("policy", "jsonb", ""), ("compiled_params", "jsonb", ""),
            ("compiler_version", "text", ""), ("source_message_id", "bigint", ""),
            ("confirmed_by_user", "bool", ""), ("created_at", "timestamptz", ""),
        ]),
        "pa_portfolio_snapshots": Table("pa_portfolio_snapshots", [
            ("id", "bigint", "PK"), ("conversation_id", "uuid", "FK"), ("version", "int", ""),
            ("cash_egp", "numeric", ""), ("positions", "jsonb", ""),
            ("promoted_from_scenario", "bigint", ""), ("confirmed_by_user", "bool", ""),
            ("created_at", "timestamptz", ""),
        ]),
        "pa_scenarios": Table("pa_scenarios", [
            ("id", "bigint", "PK"), ("conversation_id", "uuid", "FK"),
            ("parent_scenario_id", "bigint", "FK"), ("base_snapshot_id", "bigint", "FK"),
            ("patch", "jsonb", ""), ("derived_positions", "jsonb", ""),
            ("derived_policy", "jsonb", ""), ("input_set", "jsonb", ""),
            ("proposal_id", "bigint", ""), ("status", "text", ""), ("label", "text", ""),
            ("created_at", "timestamptz", ""),
        ]),
        "pa_optimization_proposals": Table("pa_optimization_proposals", [
            ("id", "bigint", "PK"), ("conversation_id", "uuid", "FK"),
            ("snapshot_id", "bigint", "FK"), ("scenario_id", "bigint", "FK"),
            ("policy_id", "bigint", "FK"), ("inputs", "jsonb", ""), ("proposal", "jsonb", ""),
            ("solver_status", "text", ""), ("engine_version", "text", ""),
            ("created_at", "timestamptz", ""),
        ]),
    })
    return t


# ----------------------------------------------------------------------------- SVG builder
class ERDRenderer:
    def __init__(self, tables: dict[str, Table], *, title: str, canvas_w: float, canvas_h: float):
        self.tables = tables
        self.title = title
        self.canvas_w = canvas_w
        self.canvas_h = canvas_h
        self.connectors: list[str] = []
        self.table_svg: list[str] = []

    @staticmethod
    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def draw_table(self, t: Table) -> None:
        self.table_svg.append("<g>")
        self.table_svg.append(
            f'<rect x="{t.x}" y="{t.y}" width="{W}" height="{t.h}" '
            f'rx="4" fill="{ROW_FILL}" stroke="{INK}" stroke-width="1.6"/>'
        )
        self.table_svg.append(
            f'<rect x="{t.x}" y="{t.y}" width="{W}" height="{TITLE_H}" '
            f'rx="4" fill="{HEADER_FILL}" stroke="{INK}" stroke-width="1.6"/>'
        )
        self.table_svg.append(
            f'<rect x="{t.x}" y="{t.y + TITLE_H - 4}" width="{W}" height="4" fill="{HEADER_FILL}"/>'
        )
        self.table_svg.append(
            f'<text x="{t.cx}" y="{t.y + TITLE_H / 2 + 4.5}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="13" font-weight="700" fill="{INK}">'
            f"{self.esc(t.name)}</text>"
        )
        if t.subtitle:
            self.table_svg.append(
                f'<text x="{t.cx}" y="{t.y + t.h + 14}" text-anchor="middle" '
                f'font-family="{FONT}" font-size="9.5" font-style="italic" fill="#444">'
                f"{self.esc(t.subtitle)}</text>"
            )
        for i, (c, typ, key) in enumerate(t.cols):
            ry = t.y + TITLE_H + i * ROW_H
            is_key = key in ("PK", "PFK")
            fill = PK_FILL if is_key else (ALT_FILL if i % 2 else ROW_FILL)
            self.table_svg.append(
                f'<rect x="{t.x}" y="{ry}" width="{W}" height="{ROW_H}" fill="{fill}"/>'
            )
            weight = "700" if is_key else "400"
            deco = ' text-decoration="underline"' if is_key else ""
            self.table_svg.append(
                f'<text x="{t.x + 8}" y="{ry + ROW_H / 2 + 4}" font-family="{MONO}" '
                f'font-size="10.5" font-weight="{weight}" fill="{INK}"{deco}>{self.esc(c)}</text>'
            )
            self.table_svg.append(
                f'<text x="{t.x + W - 8}" y="{ry + ROW_H / 2 + 4}" text-anchor="end" '
                f'font-family="{MONO}" font-size="9" fill="#555">{self.esc(typ)}</text>'
            )
            marker = {"PK": "PK", "FK": "FK", "UQ": "UQ"}.get(key, "")
            if marker:
                self.table_svg.append(
                    f'<text x="{t.x + W - 58}" y="{ry + ROW_H / 2 + 4}" text-anchor="end" '
                    f'font-family="{MONO}" font-size="7.5" font-weight="700" fill="{INK}">'
                    f"{marker}</text>"
                )
            ly = ry + ROW_H
            self.table_svg.append(
                f'<line x1="{t.x}" y1="{ly}" x2="{t.x + W}" y2="{ly}" '
                f'stroke="#cfcfcf" stroke-width="0.5"/>'
            )
        self.table_svg.append("</g>")

    @staticmethod
    def crowfoot(px, py, direction, spread=8, length=15):
        dirs = {
            "left": [(px, py - spread), (px, py + spread), (px, py)],
            "right": [(px, py - spread), (px, py + spread), (px, py)],
            "top": [(px - spread, py), (px + spread, py), (px, py)],
            "bottom": [(px - spread, py), (px + spread, py), (px, py)],
        }
        offsets = {"left": (-length, 0), "right": (length, 0), "top": (0, -length), "bottom": (0, length)}
        ax = px + offsets[direction][0]
        ay = py + offsets[direction][1]
        return "".join(
            f'<line x1="{ax}" y1="{ay}" x2="{ex}" y2="{ey}" stroke="{INK}" stroke-width="1.4"/>'
            for ex, ey in dirs[direction]
        )

    @staticmethod
    def onebar(px, py, direction, off=11, half=7):
        if direction in ("left", "right"):
            bx = px - off if direction == "left" else px + off
            return f'<line x1="{bx}" y1="{py - half}" x2="{bx}" y2="{py + half}" stroke="{INK}" stroke-width="1.6"/>'
        by = py - off if direction == "top" else py + off
        return f'<line x1="{px - half}" y1="{by}" x2="{px + half}" y2="{by}" stroke="{INK}" stroke-width="1.6"/>'

    def path(self, pts, dashed=False):
        d = "M " + " L ".join(f"{x},{y}" for x, y in pts)
        dash = ' stroke-dasharray="6,4"' if dashed else ""
        self.connectors.append(
            f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="1.4"{dash}/>'
        )

    def connect_h(self, parent, pcol, child, ccol, dashed=False):
        p, c = self.tables[parent], self.tables[child]
        py, cy = p.row_y(pcol), c.row_y(ccol)
        px, cx = p.x + W, c.x
        midx = (px + cx) / 2
        self.path([(px, py), (midx, py), (midx, cy), (cx, cy)], dashed=dashed)
        self.connectors.append(self.onebar(px, py, "right"))
        self.connectors.append(self.crowfoot(cx, cy, "left"))

    def connect_v_split(self, parent, child, ccol, bus_y, dashed=False):
        """Parent bottom -> horizontal bus -> child top."""
        p, c = self.tables[parent], self.tables[child]
        ccx = c.cx
        self.path([(p.cx, p.y + p.h), (p.cx, bus_y), (ccx, bus_y), (ccx, c.y)], dashed=dashed)
        self.connectors.append(self.onebar(p.cx, p.y + p.h, "bottom"))
        self.connectors.append(self.crowfoot(ccx, c.y, "top"))

    def zone_label(self, out, x, y, txt):
        out.append(
            f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="12" font-weight="700" '
            f'fill="#333" letter-spacing="1.2">{self.esc(txt)}</text>'
        )

    def legend(self, out, lx, ly):
        out.append(
            f'<rect x="{lx}" y="{ly}" width="300" height="168" rx="6" fill="#fbfbfb" '
            f'stroke="{INK}" stroke-width="1.2"/>'
        )
        out.append(
            f'<text x="{lx + 14}" y="{ly + 22}" font-family="{FONT}" font-size="12.5" '
            f'font-weight="700" fill="{INK}">Legend</text>'
        )
        out.append(f'<line x1="{lx + 18}" y1="{ly + 44}" x2="{lx + 68}" y2="{ly + 44}" stroke="{INK}" stroke-width="1.4"/>')
        out.append(self.onebar(lx + 60, ly + 44, "right"))
        out.append(
            f'<text x="{lx + 82}" y="{ly + 48}" font-family="{FONT}" font-size="10.5" fill="{INK}">'
            f"one (PK side)</text>"
        )
        out.append(f'<line x1="{lx + 18}" y1="{ly + 68}" x2="{lx + 60}" y2="{ly + 68}" stroke="{INK}" stroke-width="1.4"/>')
        out.append(self.crowfoot(lx + 60, ly + 68, "right"))
        out.append(
            f'<text x="{lx + 88}" y="{ly + 72}" font-family="{FONT}" font-size="10.5" fill="{INK}">'
            f"many (FK side)</text>"
        )
        out.append(
            f'<line x1="{lx + 18}" y1="{ly + 96}" x2="{lx + 88}" y2="{ly + 96}" stroke="{INK}" '
            f'stroke-width="1.4" stroke-dasharray="6,4"/>'
        )
        out.append(
            f'<text x="{lx + 98}" y="{ly + 100}" font-family="{FONT}" font-size="10.5" fill="{INK}">'
            f"logical ref (no DB FK)</text>"
        )
        out.append(
            f'<text x="{lx + 18}" y="{ly + 124}" font-family="{FONT}" font-size="10" fill="#444">'
            f"Agent memory: ChromaDB (not in Postgres)</text>"
        )
        out.append(
            f'<text x="{lx + 18}" y="{ly + 142}" font-family="{FONT}" font-size="10" fill="#444">'
            f"Portfolio Assistant: separate pa_* schema</text>"
        )

    def render(self) -> str:
        out = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.canvas_w} {self.canvas_h}" '
            f'font-family="{FONT}">',
            f'<rect x="0" y="0" width="{self.canvas_w}" height="{self.canvas_h}" fill="#ffffff"/>',
            f'<text x="36" y="34" font-family="{FONT}" font-size="20" font-weight="700" fill="{INK}">'
            f"{self.esc(self.title)}</text>",
        ]
        for c in self.connectors:
            out.append(c)
        for t in self.tables.values():
            self.draw_table(t)
        out.extend(self.table_svg)
        out.append("</svg>")
        return "\n".join(out)


def layout_thesis(tables: dict[str, Table]) -> tuple[float, float]:
    """Six-table layout — three horizontal bands, no crossing edges."""
    # Row 1: users (centre)
    tables["users"].x, tables["users"].y = 430, 58
    # Row 2: audit cluster (left) + backtest cluster (right)
    tables["analysis_sessions"].x, tables["analysis_sessions"].y = 36, 260
    tables["agent_events"].x, tables["agent_events"].y = 380, 260
    tables["backtest_runs"].x, tables["backtest_runs"].y = 780, 260
    tables["backtest_trades"].x, tables["backtest_trades"].y = 1124, 260
    # Row 3: standalone social archive
    tables["social_v2_posts"].x, tables["social_v2_posts"].y = 430, 640
    return 1420, 920


def wire_thesis(r: ERDRenderer) -> None:
    t = r.tables
    # Enforced FKs
    r.connect_h("analysis_sessions", "session_id", "agent_events", "session_id")
    r.connect_h("backtest_runs", "run_id", "backtest_trades", "run_id")
    # Logical user ownership (dashed — FK not enforced in DB yet)
    r.connect_v_split("users", "analysis_sessions", "user_id", bus_y=210, dashed=True)
    r.connect_v_split("users", "backtest_runs", "user_id", bus_y=210, dashed=True)


def layout_full(tables: dict[str, Table]) -> tuple[float, float]:
    tables["analysis_sessions"].x, tables["analysis_sessions"].y = 40, 60
    tables["agent_events"].x, tables["agent_events"].y = 360, 60
    tables["backtest_runs"].x, tables["backtest_runs"].y = 700, 60
    tables["backtest_trades"].x, tables["backtest_trades"].y = 1020, 60
    tables["users"].x, tables["users"].y = 1340, 60
    tables["agent_memories"].x, tables["agent_memories"].y = 40, 470
    tables["ohlcv_prices"].x, tables["ohlcv_prices"].y = 360, 470
    tables["cache_entries"].x, tables["cache_entries"].y = 680, 470
    tables["social_v2_posts"].x, tables["social_v2_posts"].y = 1020, 470
    PA_Y = 1010
    tables["pa_conversations"].x, tables["pa_conversations"].y = 40, PA_Y + 30
    pa_row = [
        "pa_messages", "pa_events", "pa_policies",
        "pa_portfolio_snapshots", "pa_scenarios", "pa_optimization_proposals",
    ]
    for i, n in enumerate(pa_row):
        tables[n].x = 420 + i * 290
        tables[n].y = PA_Y
    return 420 + 5 * 290 + W + 40, 1430


def wire_full(r: ERDRenderer) -> None:
    r.connect_h("analysis_sessions", "session_id", "agent_events", "session_id")
    r.connect_h("backtest_runs", "run_id", "backtest_trades", "run_id")
    PA_Y = 1010
    pa_row = [
        "pa_messages", "pa_events", "pa_policies",
        "pa_portfolio_snapshots", "pa_scenarios", "pa_optimization_proposals",
    ]
    TRUNK_Y = PA_Y - 46
    for child in pa_row:
        p, c = r.tables["pa_conversations"], r.tables[child]
        r.path([(p.cx, p.y), (p.cx, TRUNK_Y), (c.cx, TRUNK_Y), (c.cx, c.y)])
        r.connectors.append(r.onebar(p.cx, p.y, "top"))
        r.connectors.append(r.crowfoot(c.cx, c.y, "top"))


def export_png(svg_path: str, png_path: str) -> bool:
    for cmd in (
        ["cairosvg", svg_path, "-o", png_path, "-d", "300"],
        ["rsvg-convert", "-d", "300", "-p", "300", svg_path, "-o", png_path],
    ):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                return True
            except subprocess.CalledProcessError:
                pass
    try:
        import cairosvg  # noqa: F401
        cairosvg.svg2png(url=svg_path, write_to=png_path, dpi=300)
        return True
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate EGX database ERD diagrams")
    parser.add_argument(
        "--full", action="store_true",
        help="Include agent_memories, ohlcv, cache, and pa_* tables (legacy diagram)",
    )
    parser.add_argument(
        "--thesis", action="store_true",
        help="Core 6-table thesis diagram (default)",
    )
    args = parser.parse_args()
    thesis_mode = not args.full

    os.makedirs("docs/erd", exist_ok=True)

    if thesis_mode:
        tables = _thesis_tables()
        cw, ch = layout_thesis(tables)
        title = "EGX Multi-Agent Trading System — PostgreSQL Schema (Thesis ERD)"
        stem = "egx_thesis_erd"
    else:
        tables = _full_tables()
        cw, ch = layout_full(tables)
        title = "EGX Multi-Agent Trading System — Full Database Schema (ERD)"
        stem = "egx_database_erd"

    r = ERDRenderer(tables, title=title, canvas_w=cw, canvas_h=ch)
    if thesis_mode:
        wire_thesis(r)
    else:
        wire_full(r)

    svg_path = f"docs/erd/{stem}.svg"
    png_path = f"docs/erd/{stem}.png"

    svg_body = r.render()
    # inject zone labels + legend before closing tables (prepend to render output)
    parts = svg_body.split("</svg>")
    header = parts[0]
    if thesis_mode:
        zone_lines = [
            f'<text x="36" y="52" font-family="{FONT}" font-size="11" font-weight="700" fill="#333" letter-spacing="1">IDENTITY</text>',
            f'<text x="36" y="248" font-family="{FONT}" font-size="11" font-weight="700" fill="#333" letter-spacing="1">ANALYSIS &amp; AUDIT TRAIL</text>',
            f'<text x="780" y="248" font-family="{FONT}" font-size="11" font-weight="700" fill="#333" letter-spacing="1">BACKTEST PERSISTENCE</text>',
            f'<text x="430" y="628" font-family="{FONT}" font-size="11" font-weight="700" fill="#333" letter-spacing="1">SOCIAL / NEWS ARCHIVE (standalone)</text>',
        ]
        legend_block = []
        r.legend(legend_block, lx=cw - 330, ly=58)
        header = header + "\n" + "\n".join(zone_lines) + "\n" + "\n".join(legend_block)

    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(header + "</svg>")

    print(f"wrote {svg_path}  ({len(tables)} tables, canvas {cw}x{ch})")

    if export_png(svg_path, png_path):
        print(f"wrote {png_path}")
    else:
        print("PNG skipped (install cairosvg or rsvg-convert for raster export)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
