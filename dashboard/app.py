import json, glob
from pathlib import Path
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
import hashlib, hmac, os

st.set_page_config(page_title="A-SWARM KPI Board", layout="wide")
st.title("A-SWARM — Prototype KPI Board")
st.caption("Live view of Action Certificates (MTTD, MTTR, targets, and optional signature verification)")

# --- Sidebar controls ---
cert_dir = st.sidebar.text_input("Certificates folder", "./ActionCertificates")
selector = st.sidebar.text_input("Selector filter (e.g., app=anomaly)", "")
namespace = st.sidebar.text_input("Namespace filter", "")
hmac_key = st.sidebar.text_input("HMAC key (optional, to verify certs)", value="", type="password")
show_targets = st.sidebar.checkbox("Show KPI target lines", True)
st.sidebar.markdown("**Targets:** MTTD P95 ≤ **200 ms**, MTTR P95 ≤ **5 s**")

# --- Load certificates (cached for snappy reloads) ---
@st.cache_data(ttl=2)
def load_certs(cert_dir: str):
    paths = sorted(glob.glob(str(Path(cert_dir) / "*.json")))
    rows, raw = [], {}
    for p in paths:
        try:
            data = json.loads(Path(p).read_text())
            raw[p] = data
            ts = data.get("timestamps", {})
            # Compute MTTD / MTTR from timestamps if present
            mttd = mttr = None
            if ts.get("anomaly_start") and ts.get("detect_elevated"):
                t0 = pd.to_datetime(ts["anomaly_start"], utc=True)
                t1 = pd.to_datetime(ts["detect_elevated"], utc=True)
                mttd = (t1 - t0).total_seconds() * 1000.0
            if ts.get("detect_elevated") and ts.get("actuation_effective"):
                t1 = pd.to_datetime(ts["detect_elevated"], utc=True)
                t2 = pd.to_datetime(ts["actuation_effective"], utc=True)
                mttr = (t2 - t1).total_seconds()
            # Optional richer elevation context if you wrote it
            elev = data.get("elevation_context", {})
            row = {
                "file": p,
                "certificate_id": data.get("certificate_id"),
                "namespace": data.get("site_id"),
                "asset_id": data.get("asset_id"),
                "policy_id": data.get("policy", {}).get("policy_id"),
                "selector": data.get("action", {}).get("params", {}).get("selector", ""),
                "scenario": elev.get("scenario") or data.get("scenario", ""),
                "window_s": elev.get("window_seconds"),
                "witnesses": elev.get("witnesses"),
                "count": elev.get("count"),
                "t0_anomaly": ts.get("anomaly_start"),
                "t1_detect": ts.get("detect_elevated"),
                "t2_effective": ts.get("actuation_effective"),
                "MTTD_ms": mttd,
                "MTTR_s": mttr,
            }
            rows.append(row)
        except Exception:
            continue
    df = pd.DataFrame(rows)
    return df, raw

df, raw = load_certs(cert_dir)

# Filters
if selector:
    df = df[df["selector"].astype(str).str.contains(selector)]
if namespace:
    df = df[df["namespace"] == namespace]

st.subheader("Latest runs")
if len(df) == 0:
    st.info("No certificates found yet. Run a drill to generate ActionCertificates/*.json.")
else:
    st.dataframe(
        df.sort_values("t2_effective", ascending=False) if "t2_effective" in df.columns else df,
        use_container_width=True
    )

# --- KPI summary / compliance ---
def pct(series, p):  # percentile helper
    if len(series) == 0: return None
    return float(pd.Series(series).quantile(p/100.0))

mttd_series = df.dropna(subset=["MTTD_ms"]) if "MTTD_ms" in df.columns else pd.DataFrame()
mttr_series = df.dropna(subset=["MTTR_s"]) if "MTTR_s" in df.columns else pd.DataFrame()
mttd_targets = {"p50": pct(mttd_series["MTTD_ms"] if len(mttd_series) > 0 else [], 50),
                "p95": pct(mttd_series["MTTD_ms"] if len(mttd_series) > 0 else [], 95),
                "p99": pct(mttd_series["MTTD_ms"] if len(mttd_series) > 0 else [], 99)}
mttr_targets = {"p50": pct(mttr_series["MTTR_s"] if len(mttr_series) > 0 else [], 50),
                "p95": pct(mttr_series["MTTR_s"] if len(mttr_series) > 0 else [], 95),
                "p99": pct(mttr_series["MTTR_s"] if len(mttr_series) > 0 else [], 99)}

def compliance(series, threshold, less_is_better=True):
    if len(series) == 0: return None
    s = pd.Series(series).dropna()
    ok = (s <= threshold).mean() if less_is_better else (s >= threshold).mean()
    return round(100.0 * ok, 1)

colA, colB = st.columns(2)

with colA:
    st.markdown("### MTTD (ms)")
    if len(mttd_series) == 0:
        st.info("No MTTD data yet")
    else:
        fig = plt.figure()
        xs = pd.to_datetime(mttd_series["t1_detect"])
        ys = mttd_series["MTTD_ms"]
        plt.plot(xs, ys, marker="o", linestyle="-")
        if show_targets:
            plt.axhline(200, color='red', linestyle="--", label="Target: 200ms")
            plt.legend()
        plt.xlabel("detect time")
        plt.ylabel("ms")
        plt.xticks(rotation=45)
        plt.tight_layout()
        st.pyplot(fig, use_container_width=True)
        st.write({
            "p50": mttd_targets["p50"],
            "p95": mttd_targets["p95"],
            "p99": mttd_targets["p99"],
            "≤200ms_compliance_%": compliance(mttd_series["MTTD_ms"], 200.0),
        })

with colB:
    st.markdown("### MTTR (s)")
    if len(mttr_series) == 0:
        st.info("No MTTR data yet")
    else:
        fig2 = plt.figure()
        xs = pd.to_datetime(mttr_series["t2_effective"])
        ys = mttr_series["MTTR_s"]
        plt.plot(xs, ys, marker="o", linestyle="-")
        if show_targets:
            plt.axhline(5.0, color='red', linestyle="--", label="Target: 5s")
            plt.legend()
        plt.xlabel("containment effective")
        plt.ylabel("s")
        plt.xticks(rotation=45)
        plt.tight_layout()
        st.pyplot(fig2, use_container_width=True)
        st.write({
            "p50": mttr_targets["p50"],
            "p95": mttr_targets["p95"],
            "p99": mttr_targets["p99"],
            "≤5s_compliance_%": compliance(mttr_series["MTTR_s"], 5.0),
        })

# --- Certificate details & signature verification ---
st.markdown("---")
st.subheader("Certificate details")

if len(df) > 0:
    # Pick the most recent by default
    default_idx = df.sort_values("t2_effective", ascending=False).index[:1] if "t2_effective" in df.columns else df.index[:1]
    selected_id = st.selectbox(
        "Select certificate_id",
        options=df["certificate_id"].tolist(),
        index=0 if len(default_idx)==0 else df.index.get_loc(default_idx[0])
    )
    sel_row = df[df["certificate_id"] == selected_id].iloc[0]
    cert_path = sel_row["file"]
    cert_json = raw.get(cert_path, {})
    
    # Show certificate JSON
    st.code(json.dumps(cert_json, indent=2), language="json")

    # Signature verification
    if hmac_key:
        try:
            body = Path(cert_path).read_bytes()
            computed = hmac.new(hmac_key.encode(), body, hashlib.sha256).hexdigest()
            # The measure_mttr.py script returns signature in the output, not in the cert itself
            # So we need to check if there's a signature field or look for it elsewhere
            # For now, let's show the computed signature
            st.info(f"Computed HMAC-SHA256: {computed}")
            st.caption("Note: Signature verification requires the signature to be stored with the certificate or passed separately")
        except Exception as e:
            st.error(f"Signature check failed: {e}")

    # Download button
    st.download_button(
        "Download this certificate JSON", 
        data=json.dumps(cert_json, indent=2), 
        file_name=f"{selected_id}.json",
        mime="application/json"
    )

# Export all data
st.markdown("---")
if len(df) > 0:
    csv = df.to_csv(index=False)
    st.download_button(
        "Export all data as CSV",
        data=csv,
        file_name="aswarm_certificates.csv",
        mime="text/csv"
    )

st.caption("Tip: run multiple drills to populate percentiles; toggle KPI target lines in the sidebar.")