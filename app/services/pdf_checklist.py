"""The at-a-glance page of the premium PDF: every check the report ran,
its one-line result, a status and the source that published it.

The live report shows the same data as forty cards a reader opens one
at a time. On paper the reader wants the opposite: one table that
answers every check on a single page, then the detail behind it. This
module builds that table from the same gathered dataset the report and
the running-costs page already use, so the three can never disagree.

Missing data is a row with words in it, never a blank, which is the
site's rule everywhere else too.
"""
from __future__ import annotations


def _fmt_gbp(value, decimals: int = 0) -> str:
    if value is None:
        return ""
    return f"£{value:,.{decimals}f}"


def _status(label: str | None, good=(), bad=(), warn=()) -> str:
    """Status from a label's wording. Order matters: a label such as
    "Very low risk" must not match "high" inside "highly"."""
    text = (label or "").lower()
    if not text:
        return "neutral"
    for w in bad:
        if w in text:
            return "bad"
    for w in warn:
        if w in text:
            return "warn"
    for w in good:
        if w in text:
            return "good"
    return "neutral"


RISK_GOOD = ("improbable", "very low", "low", "none", "clear", "not in", "no active", "zone 1")
RISK_WARN = ("moderate", "possible", "medium", "zone 2")
RISK_BAD = ("very high", "high", "likely", "probable", "on the site", "zone 3")


def build(report: dict, rc: dict | None, stamp_duty: dict | None = None) -> list[dict]:
    """stamp_duty: the route's calculation on the valuation estimate,
    preferred over the running-costs page's figure on the last sale,
    which for a home last sold in 1996 is a tax on a 1996 price."""
    rc = rc or {}
    rows: list[dict] = []

    def add(group: str, check: str, result: str, status: str = "neutral", source: str = "") -> None:
        rows.append({"group": group, "check": check, "result": result, "status": status, "source": source})

    # ---- Value and market ------------------------------------------------
    sales = rc.get("sales") or {}
    tx = report.get("transactions") or []
    if sales.get("latest_year") and sales.get("latest_amount"):
        result = f"Last sale {_fmt_gbp(sales['latest_amount'])} in {sales['latest_year']}"
        if sales.get("median_recent") and sales.get("recent_n", 0) >= 3:
            result += f"; recent median {_fmt_gbp(sales['median_recent'])} across {sales['recent_n']} sales"
        add("Value and market", "Sold prices at this postcode", result, "neutral", "HM Land Registry")
    elif tx:
        add("Value and market", "Sold prices at this postcode", f"{len(tx)} recorded sales", "neutral", "HM Land Registry")
    else:
        add("Value and market", "Sold prices at this postcode", "No recorded sales at this postcode", "neutral", "HM Land Registry")

    val = report.get("valuation")
    if val and val.get("estimate"):
        add("Value and market", "Valuation estimate",
            f"{_fmt_gbp(val['estimate'])}, range {_fmt_gbp(val['low'])} to {_fmt_gbp(val['high'])}, from {val.get('sample_size', 0)} comparable sales",
            "neutral", "HM Land Registry comparables")
    else:
        add("Value and market", "Valuation estimate", "Not enough comparable sales nearby to estimate", "neutral", "HM Land Registry")

    pt = report.get("price_trend")
    if pt and pt.get("pct_change") is not None:
        pct = pt["pct_change"]
        add("Value and market", f"Price trend, five years, {pt.get('area_name', '')}".strip(", "),
            f"{pct:+.1f}%: {_fmt_gbp(pt.get('start_price'))} to {_fmt_gbp(pt.get('current_price'))}",
            "good" if pct >= 0 else "warn", "HM Land Registry house price index")
    else:
        add("Value and market", "Price trend, five years", "No index series for this authority", "neutral", "HM Land Registry house price index")

    hpi = report.get("hpi") or {}
    la = hpi.get("local_authority")
    if la and la.get("annual_change_pct") is not None:
        add("Value and market", f"Area prices, {la.get('name', '')}".strip(", "),
            f"Average {_fmt_gbp(la.get('average_price'))}, {la['annual_change_pct']:+.1f}% on a year ago",
            "good" if la["annual_change_pct"] >= 0 else "warn", "HM Land Registry house price index")

    rent = report.get("rental") or rc.get("rent")
    if rent and rent.get("price_all"):
        add("Value and market", f"Private rents, {rent.get('la_name', '')}".strip(", "),
            f"{_fmt_gbp(rent['price_all'])} a month across all sizes" + (f", {rent['change_all_pct']:+.1f}% on a year ago" if rent.get("change_all_pct") is not None else ""),
            "neutral", "ONS Price Index of Private Rents")
    else:
        add("Value and market", "Private rents", "No rent index for this authority", "neutral", "ONS")

    nb = report.get("new_build_stat")
    if nb and nb.get("total"):
        add("Value and market", "New-build share of local sales",
            f"{nb['pct']}% of {nb['total']} sales in the last {nb.get('years', 3)} years were new builds", "neutral", "HM Land Registry")

    # ---- Running costs ---------------------------------------------------
    ct = rc.get("council_tax") or report.get("council_tax")
    if ct and ct.get("band_d"):
        add("Running costs", f"Council tax, {ct.get('authority', '')} {ct.get('year', '')}".strip(),
            f"{_fmt_gbp(ct['band_d'])} a year at Band D; the home's own band is on its bill", "neutral", "MHCLG, Welsh and Scottish Governments")
    else:
        add("Running costs", "Council tax", "No published figure for this authority", "neutral", "MHCLG")

    home = rc.get("home") or {}
    energy = rc.get("energy") or {}
    if home.get("energy_now"):
        result = f"{_fmt_gbp(home['energy_now'])} a year for this home, the EPC assessor's estimate"
        if home.get("energy_potential") and home["energy_potential"] < home["energy_now"]:
            result += f"; {_fmt_gbp(home['energy_potential'])} after its recommended improvements"
        add("Running costs", "Energy: heating, hot water, lighting", result, "neutral", "EPC Register")
    elif energy.get("median"):
        add("Running costs", "Energy: heating, hot water, lighting",
            f"{_fmt_gbp(energy['median'])} a year, the middle of {energy.get('priced', 0)} homes with a certificate at this postcode", "neutral", "EPC Register")
    else:
        add("Running costs", "Energy: heating, hot water, lighting", "No EPC with costs found at this postcode", "neutral", "EPC Register")

    sd = rc.get("stamp_duty") or {}
    if stamp_duty and stamp_duty.get("price"):
        sd = {**stamp_duty, "basis": "the valuation estimate"}
    if sd.get("devolved"):
        add("Running costs", "Stamp duty", "Devolved: Land Transaction Tax in Wales, LBTT in Scotland; not calculated here", "neutral", "HMRC")
    elif sd.get("price"):
        ftb = f", {_fmt_gbp(sd['first_time'])} first-time buyer" if sd.get("first_time") is not None else ", no first-time relief above £500,000"
        add("Running costs", "Stamp duty, one-off",
            f"{_fmt_gbp(sd.get('standard'))} moving home{ftb}, {_fmt_gbp(sd.get('additional'))} for an additional property, on {_fmt_gbp(sd['price'])} ({sd.get('basis', '')})",
            "neutral", "HMRC rates from April 2025")

    if home.get("tenure"):
        ten = home["tenure"]
        add("Running costs", "Tenure at the last recorded sale", ten.capitalize() + (": ground rent and a service charge apply, set in the lease" if ten == "leasehold" else ""),
            "warn" if ten == "leasehold" else "good", "HM Land Registry")
    elif sales.get("counts"):
        c = sales["counts"]
        add("Running costs", "Tenure at this postcode", f"{c.get('freehold', 0)} freehold and {c.get('leasehold', 0)} leasehold of {sales.get('sales', 0)} recorded sales", "neutral", "HM Land Registry")

    add("Running costs", "Estate charge", "No official source publishes what a managed estate charges; ask the seller for the last two years of demands", "neutral", "Companies House register of management companies")

    # ---- The property ----------------------------------------------------
    pd = report.get("property_detail")
    if pd and pd.get("current_band"):
        result = f"Band {pd['current_band']}, score {pd.get('current_score', '')}"
        if pd.get("potential_band"):
            result += f"; could reach {pd['potential_band']}"
        if pd.get("inspection_date"):
            result += f"; certificate dated {pd['inspection_date']}"
        add("The property", "Energy performance certificate", result,
            "good" if pd["current_band"] in "ABC" else ("warn" if pd["current_band"] in "DE" else "bad"), "EPC Register")
        add("The property", "Size and layout",
            (f"{pd['dwelling_type']}, " if pd.get("dwelling_type") else "") + (f"{pd['total_floor_area']} sq m, " if pd.get("total_floor_area") else "") + (f"{pd['habitable_room_count']} habitable rooms" if pd.get("habitable_room_count") else ""),
            "neutral", "EPC Register")
        add("The property", "Year built", pd.get("year_built") or "Not recorded on the certificate", "neutral", "EPC Register")
        plan = pd.get("improvements") or {}
        to_c = plan.get("to_c")
        if plan.get("already_c"):
            add("The property", "Cost to reach EPC Band C", f"Already Band {pd['current_band']}", "good", "EPC Register, the certificate's recommendations")
        elif to_c and to_c.get("cost_low") is not None:
            saving = f", saving about {_fmt_gbp(to_c['saving'])} a year" if to_c.get("saving") else ""
            add("The property", "Cost to reach EPC Band C",
                f"{_fmt_gbp(to_c['cost_low'])} to {_fmt_gbp(to_c['cost_high'])} for {to_c['count']} of the certificate's {len(plan['steps'])} measures{saving}",
                "warn" if pd["current_band"] in "DEFG" else "neutral", "EPC Register, the certificate's recommendations")
        elif plan.get("steps"):
            whole = plan["all"]
            cost = f" for {_fmt_gbp(whole['cost_low'])} to {_fmt_gbp(whole['cost_high'])}" if whole.get("cost_low") is not None else ""
            add("The property", "Cost to reach EPC Band C", f"Not reached: every measure on the certificate gets to Band {whole['band_after']}{cost}", "warn", "EPC Register, the certificate's recommendations")
        elif "improvements" in pd:
            add("The property", "Cost to reach EPC Band C", "The certificate lists no recommended measures", "neutral", "EPC Register")
    else:
        add("The property", "Energy performance certificate", "No certificate found for this address; add a house number if you have one", "neutral", "EPC Register")

    mees = report.get("mees_compliant")
    if mees is not None:
        add("The property", "Lettable (MEES minimum E)", "Yes" if mees else "No, rated F or G: cannot be let without improvement", "good" if mees else "bad", "EPC Register")
    if report.get("lead_plumbing_era"):
        add("The property", "Lead plumbing era", "Built before about 1970: original lead pipework possible, ask the surveyor", "warn", "EPC Register age band")
    ext = report.get("extension_signal")
    if ext and ext.get("likely_extended"):
        add("The property", "Possible unrecorded extension", f"Floor area grew {ext['change_pct']:+.0f}% between certificates", "warn", "EPC Register")
    orient = report.get("orientation")
    if orient and orient.get("rear_facing"):
        add("The property", "Aspect", f"Front faces {orient.get('front_facing', '')}, rear garden faces {orient['rear_facing']}" + (f", off {orient['nearest_road']}" if orient.get("nearest_road") else ""),
            "good" if orient["rear_facing"] in ("South", "South-West", "South-East", "West") else "neutral", "OpenStreetMap building footprint")

    # ---- Risk and safety -------------------------------------------------
    fz = report.get("flood_zone")
    fz_label = fz.get("label") if fz else "Zone 1 (low probability)"
    add("Risk and safety", "Flood zone, rivers and sea", fz_label + ("; " + f"{len(report['flood_warnings'])} active warning(s)" if report.get("flood_warnings") else ""),
        _status(fz_label, RISK_GOOD, RISK_BAD, RISK_WARN), "Environment Agency")
    sw = report.get("surface_water")
    if sw:
        add("Risk and safety", "Surface water flooding", sw.get("label", "") + (f", {sw['probability']}" if sw.get("probability") else ""), _status(sw.get("label"), RISK_GOOD, RISK_BAD, RISK_WARN), "Environment Agency")
    else:
        add("Risk and safety", "Surface water flooding", "No mapped risk band at this point", "neutral", "Environment Agency")
    so = report.get("sewage_outfalls")
    if so:
        spills = sum(int(o.get("spill_count") or 0) for o in so)
        add("Risk and safety", "Storm overflows nearby", f"{len(so)} within range, {spills} spill(s) in {so[0].get('year', 'the last reported year')}", "warn" if spills else "neutral", "Environment Agency event duration monitoring")
    else:
        add("Risk and safety", "Storm overflows nearby", "None within range", "good", "Environment Agency")
    noise = report.get("noise")
    if noise and any(noise.get(k) is not None for k in ("road_db", "rail_db", "airport_db")):
        worst = max((noise.get(k) or 0, lab) for k, lab in (("road_db", "road"), ("rail_db", "rail"), ("airport_db", "aircraft")))
        label = noise.get({"road": "road_label", "rail": "rail_label", "aircraft": "airport_label"}[worst[1]]) or ""
        add("Risk and safety", "Noise", f"{label} at its loudest: {worst[0]} dB(A) from {worst[1]}", _status(label, ("low",), ("very high", "high"), ("moderate",)), "Defra strategic noise mapping")
    else:
        add("Risk and safety", "Noise", "Outside Defra's mapped area; not a zero reading", "neutral", "Defra")
    crime = report.get("crime")
    if crime and crime.get("total") is not None:
        dc = report.get("district_crime") or {}
        comp = ""
        if dc.get("total"):
            comp = ", lower than the wider district" if crime["total"] < dc["total"] else (", higher than the wider district" if crime["total"] > dc["total"] else ", in line with the wider district")
        add("Risk and safety", "Crime within about a mile", f"{crime['total']} recorded in {crime.get('month', 'the latest month')}{comp}", "good" if "lower" in comp else ("warn" if "higher" in comp else "neutral"), "Police.uk")
    else:
        add("Risk and safety", "Crime within about a mile", "Not available", "neutral", "Police.uk")
    radon = report.get("radon")
    if radon:
        lab = radon.get("label") if isinstance(radon, dict) else str(radon)
        add("Risk and safety", "Radon", lab, _status(lab, ("low",), ("very high", "high"), ("moderate", "elevated", "intermediate")), "UK Health Security Agency and BGS")
    clay = report.get("clay_risk")
    if clay:
        add("Risk and safety", "Subsidence, clay shrink-swell", f"{clay.get('label_2030', '')} by 2030, {clay.get('label_2050', '')} by 2050", _status(clay.get("label_2050"), RISK_GOOD, RISK_BAD, RISK_WARN), "British Geological Survey")
    coal = report.get("coal_mining")
    if coal and coal.get("present"):
        add("Risk and safety", "Coal mining reporting area", "Yes" + (f", {coal['area_name']}" if coal.get("area_name") else "") + ": a coal mining search is advisable", "warn", "Coal Authority")
    elif report.get("coal_mining_error"):
        add("Risk and safety", "Coal mining reporting area", "Service did not answer; not a clear result", "neutral", "Coal Authority")
    else:
        add("Risk and safety", "Coal mining reporting area", "Not in a reporting area", "good", "Coal Authority")
    hl = report.get("historic_landfill")
    if hl and hl.get("status") == "on_site":
        add("Risk and safety", "Historic landfill", f"On the site itself: {hl.get('site_name', '')}", "bad", "Environment Agency")
    elif hl and hl.get("status") and hl["status"] != "clear":
        add("Risk and safety", "Historic landfill", f"{hl.get('distance_m', '')} m away: {hl.get('site_name', '')}", "warn", "Environment Agency")
    else:
        add("Risk and safety", "Historic landfill", "None nearby", "good", "Environment Agency")
    aq = report.get("air_quality")
    if aq and aq.get("pollutants"):
        worst = max(aq["pollutants"], key=lambda p: p.get("times_guideline") or 0)
        x = worst.get("times_guideline") or 0
        add("Risk and safety", "Air quality", f"{worst.get('label', '')} at {x:.1f} times the WHO guideline, the worst of {len(aq['pollutants'])} pollutants ({aq.get('year', '')})",
            "bad" if x >= 3 else ("warn" if x >= 1.5 else "good"), "Defra modelled background concentrations")

    # ---- Planning and heritage ------------------------------------------
    pf = report.get("planning_flags") or []
    add("Planning and heritage", "Planning constraints", ", ".join(d.get("label", "") for d in pf) if pf else "None found at this point", "warn" if pf else "good", "planning.data.gov.uk")
    ef = report.get("environmental_flags") or []
    add("Planning and heritage", "Environmental designations", ", ".join(d.get("label", "") for d in ef) if ef else "None found at this point", "warn" if ef else "good", "Natural England")
    bf = report.get("brownfield")
    bf_source = "planning.data.gov.uk, brownfield land registers"
    if report.get("brownfield_error"):
        add("Planning and heritage", "Development sites nearby", "The planning data platform did not respond", "neutral", bf_source)
    elif bf and bf.get("covered"):
        if bf.get("count"):
            homes = f", up to {bf['dwellings']} homes where stated" if bf.get("dwellings") else ""
            perm = f", {bf['permissioned']} with permission" if bf.get("permissioned") else ""
            nearest = bf["sites"][0]
            flagged = (bf.get("dwellings") or 0) >= 10 or (bf.get("hectares") or 0) >= 0.5 or bf.get("permissioned")
            add("Planning and heritage", "Development sites nearby",
                f"{bf['count']} brownfield register site{'s' if bf['count'] != 1 else ''} within half a mile{homes}{perm}; nearest {nearest['address']}, {nearest['distance_m']} m",
                "warn" if flagged else "neutral", bf_source)
        elif bf.get("council") and not bf["council"].get("published"):
            add("Planning and heritage", "Development sites nearby", f"{bf['council']['name']} has not published its register to the national platform", "neutral", bf_source)
        else:
            add("Planning and heritage", "Development sites nearby", "None on the register within half a mile", "good", bf_source)
    elif bf is not None:
        add("Planning and heritage", "Development sites nearby", "Registers cover England only", "neutral", bf_source)
    her = report.get("heritage") or []
    if her:
        nearest = min(her, key=lambda h: h.get("distance_m") or 0)
        add("Planning and heritage", "Listed buildings nearby", f"{len(her)}; nearest {nearest.get('name', '')}, Grade {nearest.get('grade', '')}, {nearest.get('distance_m', '')} m", "neutral", "Historic England")
    else:
        add("Planning and heritage", "Listed buildings nearby", "None within range", "neutral", "Historic England")

    # ---- Schools ---------------------------------------------------------
    ls = report.get("school_landscape")
    if ls and ls.get("total_schools"):
        pct = ls.get("good_or_better_pct")
        add("Schools", f"Schools within {ls.get('radius_miles', 3)} miles", f"{ls['total_schools']} schools" + (f", {pct}% rated Good or Outstanding" if pct is not None else ""),
            "good" if (pct or 0) >= 80 else ("warn" if (pct or 0) < 60 else "neutral"), "Department for Education and Ofsted")
    else:
        add("Schools", "Schools nearby", "No school data for this address", "neutral", "Department for Education")
    cds = report.get("catchment_distance_schools") or []
    real = [s for s in cds if s.get("is_real")]
    if real:
        likely = sum(1 for s in real if (s.get("verdict") or {}).get("level") == "likely")
        add("Schools", "Admission distances", f"{len(real)} schools with a published last-admitted distance; this address is inside it for {likely}", "good" if likely else "warn", "Local authority admissions data")
    else:
        add("Schools", "Admission distances", "No published last-admitted distance for schools near here", "neutral", "Local authority admissions data")
    ind = (ls or {}).get("independent_schools") or []
    add("Schools", "Fee-paying schools", f"{len(ind)} within range" if ind else "None within range", "neutral", "Department for Education")
    he = (ls or {}).get("higher_education_names") or []
    add("Schools", "Universities", ", ".join(he) if he else "None within range", "neutral", "Department for Education")

    # ---- Getting around --------------------------------------------------
    stations = (report.get("stations_list") or {}).get("rail") or []
    if stations:
        s0 = stations[0]
        walk = f", {s0['walking_duration_min']} min walk" if s0.get("walking_duration_min") else ""
        add("Getting around", "Nearest station", f"{s0.get('name', '')}, {s0.get('distance_m', '')} m{walk}", "neutral", "OpenStreetMap and Network Rail")
    else:
        add("Getting around", "Nearest station", "No station within range", "neutral", "OpenStreetMap")
    bb = report.get("broadband") or rc.get("broadband")
    if bb:
        add("Getting around", "Broadband", f"{bb.get('label', '')}" + (f", gigabit at {bb['gigabit_pct']:.0f}% of premises" if bb.get("gigabit_pct") is not None else ""), "good" if "gigabit" in (bb.get("label") or "").lower() else "neutral", "Ofcom Connected Nations")
    mob = report.get("mobile")
    if mob and mob.get("coverage_4g_outdoor_all_pct") is not None:
        add("Getting around", "Mobile signal", f"4G outdoors {mob['coverage_4g_outdoor_all_pct']:.0f}%, indoors {mob.get('coverage_4g_indoor_all_pct') or 0:.0f}%" + (f", 5G outdoors {mob['coverage_5g_outdoor_pct']:.0f}%" if mob.get("coverage_5g_outdoor_pct") is not None else ""), "neutral", "Ofcom")
    am = report.get("amenities") or {}
    def nearest(kind):
        items = am.get(kind) or []
        return f"{items[0]['name']} {items[0]['distance_m']} m" if items else "none within range"
    add("Getting around", "Daily essentials", f"Supermarket: {nearest('supermarket')}; pharmacy: {nearest('pharmacy')}; GP: {nearest('gp')}", "neutral", "OpenStreetMap")

    # ---- Area and community ---------------------------------------------
    inc = report.get("household_income")
    if inc and inc.get("here"):
        add("Area and community", "Household income", f"{_fmt_gbp(inc['here'])} a year here, against {_fmt_gbp(inc.get('la_average'))} across {inc.get('la_name', '')}", "neutral", "ONS small area income estimates")
    dep = report.get("deprivation")
    if dep and dep.get("imd_decile"):
        add("Area and community", "Deprivation", f"Decile {dep['imd_decile']} of 10" + (f", {report['imd_label'].lower()}" if report.get("imd_label") else ""), "good" if dep["imd_decile"] >= 7 else ("warn" if dep["imd_decile"] <= 3 else "neutral"), "MHCLG Index of Multiple Deprivation")
    occ, qual = report.get("occupation"), report.get("qualification")
    if occ or qual:
        add("Area and community", "Work and education", (f"{occ['professional_pct']}% managerial or professional" if occ else "") + ("; " if occ and qual else "") + (f"{qual['degree_pct']}% degree-educated" if qual else ""), "neutral", "ONS Census 2021")
    age = report.get("age_profile")
    if age:
        add("Area and community", "Age profile", f"{age['under_25_pct']}% under 25", "neutral", "ONS Census 2021")
    hs = report.get("housing")
    if hs and hs.get("owned_pct") is not None:
        add("Area and community", "Housing tenure", f"{hs['owned_pct']}% owner-occupied", "neutral", "ONS Census 2021")
    wb = report.get("wellbeing")
    if wb and wb.get("good_health_pct") is not None:
        add("Area and community", "Health", f"{wb['good_health_pct']}% in good or very good health", "neutral", "ONS Census 2021")

    return rows


def grouped(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    order: list[str] = []
    for r in rows:
        if r["group"] not in order:
            order.append(r["group"])
    return [(g, [r for r in rows if r["group"] == g]) for g in order]
