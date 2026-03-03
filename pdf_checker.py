import sys
import os

import fitz  # PyMuPDF
from flask import Flask, render_template_string, request, jsonify
import tempfile
import re
import webbrowser
import threading
import socket
import gc

app = Flask(__name__)

app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB max

CONFIG = {
    "MAX_PAGES": 10000,
    "REQUEST_TIMEOUT": 3600,
    "PROPERTY_FEES_FILE": os.path.join(os.path.expanduser("~"), "Desktop", "property_fees.xlsx")
}

# ---------------------------------------------------------------------------
# Load property fee lookup from xlsx at startup
# ---------------------------------------------------------------------------
PROPERTY_FEES = {}  # { "PROP001": {"fee_percent": 5.0, "min_dollar_charge": 150.0}, ... }
FEES_FILE_ERROR = None

def load_property_fees():
    global PROPERTY_FEES, FEES_FILE_ERROR
    path = CONFIG["PROPERTY_FEES_FILE"]
    if not os.path.exists(path):
        FEES_FILE_ERROR = f"property_fees.xlsx not found at: {path}"
        print(f"WARNING: {FEES_FILE_ERROR}")
        return

    try:
        import pandas as pd
        df = pd.read_excel(path, sheet_name="Property Fees", dtype={"property_code": str})
        required_cols = {"property_code", "fee_percent", "min_dollar_charge"}
        if not required_cols.issubset(set(df.columns)):
            FEES_FILE_ERROR = f"property_fees.xlsx is missing required columns: {required_cols - set(df.columns)}"
            print(f"WARNING: {FEES_FILE_ERROR}")
            return

        for _, row in df.iterrows():
            code = str(row["property_code"]).strip()
            if code:
                PROPERTY_FEES[code] = {
                    "fee_percent": float(row["fee_percent"]) if pd.notna(row["fee_percent"]) else None,
                    "min_dollar_charge": float(row["min_dollar_charge"]) if pd.notna(row["min_dollar_charge"]) else None,
                }
        print(f"Loaded {len(PROPERTY_FEES)} properties from property_fees.xlsx")
    except Exception as e:
        FEES_FILE_ERROR = f"Failed to load property_fees.xlsx: {e}"
        print(f"WARNING: {FEES_FILE_ERROR}")

load_property_fees()

# ---------------------------------------------------------------------------
# HTML Template
# ---------------------------------------------------------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>🏠 PDF Property Validator</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
      max-width: 900px; margin: 0 auto; padding: 30px 20px; background: #f5f5f5;
    }
    .container { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
    h1 { color: #333; margin-bottom: 10px; font-size: 28px; }
    .subtitle { color: #666; margin-bottom: 25px; font-size: 14px; }
    .upload-section { background: #f9f9f9; padding: 20px; border-radius: 6px; margin-bottom: 30px; border: 2px dashed #ddd; }
    input[type="file"] { margin-bottom: 15px; padding: 8px; font-size: 14px; }
    button {
      background: #007bff; color: white; border: none; padding: 12px 24px;
      border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: 500; transition: background 0.2s;
    }
    button:hover:not(:disabled) { background: #0056b3; }
    button:disabled { background: #ccc; cursor: not-allowed; }
    .loader {
      display: inline-block; width: 14px; height: 14px; border: 2px solid #f3f3f3;
      border-top: 2px solid #007bff; border-radius: 50%; animation: spin 1s linear infinite;
      margin-right: 8px; vertical-align: middle;
    }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    .alert { padding: 15px; border-radius: 4px; margin-bottom: 20px; }
    .alert-error { background: #fee; color: #c33; border-left: 4px solid #c33; }
    .alert-success { background: #efe; color: #3a3; border-left: 4px solid #3a3; }
    .alert-warning { background: #fff3cd; color: #856404; border-left: 4px solid #ffc107; }
    h2 { color: #333; border-bottom: 2px solid #007bff; padding-bottom: 8px; margin: 30px 0 20px 0; font-size: 22px; }
    h3 { color: #007bff; margin: 25px 0 15px 0; font-size: 18px; }
    table { width: 100%; border-collapse: collapse; margin-top: 15px; background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    th, td { border: 1px solid #e0e0e0; padding: 12px; text-align: left; font-size: 14px; }
    th { background-color: #f8f9fa; font-weight: 600; color: #555; }
    tr:hover { background-color: #f9f9f9; }
    .status-PASS { color: #28a745; font-weight: bold; }
    .status-FAIL { color: #dc3545; font-weight: bold; }
    .status-INFO { color: #6c757d; font-style: italic; }
    .summary-table { margin-bottom: 30px; }
    .summary-table .property-name { font-weight: 600; color: #333; }
    .summary-table .failed-checks { color: #dc3545; }
    hr { margin: 40px 0; border: none; border-top: 1px solid #e0e0e0; }
    .hidden { display: none; }
    .stats { display: flex; gap: 15px; margin-bottom: 20px; }
    .stat-box { flex: 1; padding: 15px; border-radius: 6px; text-align: center; }
    .stat-box.pass { background: #d4edda; border: 1px solid #c3e6cb; }
    .stat-box.fail { background: #f8d7da; border: 1px solid #f5c6cb; }
    .stat-number { font-size: 32px; font-weight: bold; margin-bottom: 5px; }
    .stat-label { font-size: 12px; color: #666; text-transform: uppercase; }
    .footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #e0e0e0; text-align: center; color: #999; font-size: 12px; }
    .fees-status { font-size: 13px; margin-bottom: 15px; padding: 10px 14px; border-radius: 4px; }
    .fees-ok { background: #d4edda; color: #155724; border-left: 4px solid #28a745; }
    .fees-error { background: #fee; color: #c33; border-left: 4px solid #c33; }
  </style>
</head>
<body>
  <div class="container">
    <h1>🏠 PDF Property Validator</h1>
    <p class="subtitle">Upload your property statement PDF to validate cash balances, management fees, and rent roll data.</p>

    <div id="feesStatus" class="fees-status {{ 'fees-ok' if not fees_error else 'fees-error' }}">
      {% if fees_error %}
        ⚠️ Fee lookup file error: {{ fees_error }}
      {% else %}
        ✅ Fee lookup loaded: {{ fees_count }} properties from property_fees.xlsx
      {% endif %}
    </div>

    <div class="upload-section">
      <input type="file" id="file" accept="application/pdf" />
      <br>
      <button id="checkBtn" onclick="checkPDF()">
        <span id="btnText">Validate PDF</span>
      </button>
    </div>
    <div id="alert" class="hidden"></div>
    <div id="stats" class="hidden"></div>
    <hr class="hidden" id="divider">
    <div id="summary"></div>
    <div id="results"></div>
    <div class="footer">PDF Property Validator v1.1 | Close this browser window to exit the application</div>
  </div>
  <script>
    async function checkPDF() {
      const fileInput = document.getElementById("file");
      const resultsDiv = document.getElementById("results");
      const summaryDiv = document.getElementById("summary");
      const alertDiv = document.getElementById("alert");
      const statsDiv = document.getElementById("stats");
      const divider = document.getElementById("divider");
      const checkBtn = document.getElementById("checkBtn");
      const btnText = document.getElementById("btnText");

      resultsDiv.innerHTML = ""; summaryDiv.innerHTML = "";
      alertDiv.className = "hidden"; statsDiv.className = "hidden"; divider.className = "hidden";

      const file = fileInput.files[0];
      if (!file) { showAlert("Please select a PDF file first.", "error"); return; }

      checkBtn.disabled = true;
      btnText.innerHTML = '<span class="loader"></span>Processing PDF... This may take several minutes for large files';

      const formData = new FormData();
      formData.append("file", file);

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 7200000);

        const response = await fetch("/check_pdf", {
          method: "POST", body: formData, signal: controller.signal
        });
        clearTimeout(timeoutId);

        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(`Server error (${response.status}): ${errorText.substring(0, 100)}`);
        }

        const contentType = response.headers.get("content-type");
        if (!contentType || !contentType.includes("application/json")) {
          const errorText = await response.text();
          console.error("Non-JSON response:", errorText);
          throw new Error("Server returned an error instead of results. The PDF may be too large or complex.");
        }

        const data = await response.json();
        if (data.error) { showAlert(data.error, "error"); return; }

        divider.className = "";

        let totalProperties = data.detailed_checks.length;
        let propertiesWithFailures = data.failing_summary ? data.failing_summary.length : 0;
        let propertiesPassing = totalProperties - propertiesWithFailures;

        statsDiv.className = "stats";
        statsDiv.innerHTML = `
          <div class="stat-box pass">
            <div class="stat-number" style="color: #28a745;">${propertiesPassing}</div>
            <div class="stat-label">Passing</div>
          </div>
          <div class="stat-box fail">
            <div class="stat-number" style="color: #dc3545;">${propertiesWithFailures}</div>
            <div class="stat-label">Failing</div>
          </div>
        `;

        if (data.failing_summary && data.failing_summary.length > 0) {
          let summaryHtml = "<h2>⚠️ Properties with Failures</h2>";
          summaryHtml += "<table class='summary-table'>";
          summaryHtml += "<tr><th>Property</th><th>Failed Checks</th></tr>";
          data.failing_summary.forEach(prop => {
            summaryHtml += `<tr>
              <td class='property-name'>${escapeHtml(prop.property)}</td>
              <td class='failed-checks'>${escapeHtml(prop.failed_checks.join(", "))}</td>
            </tr>`;
          });
          summaryHtml += "</table>";
          summaryDiv.innerHTML = summaryHtml;
        } else {
          showAlert("🎉 All properties passed all validation checks!", "success");
        }

        let detailedHtml = "<h2>📋 Detailed Validation Results</h2>";
        data.detailed_checks.forEach(p => {
          detailedHtml += `<h3>${escapeHtml(p.property)}</h3><table>
          <tr><th>Check</th><th>Value</th><th>Expected</th><th>Status</th></tr>`;
          p.results.forEach(r => {
            const statusClass = `status-${r.status}`;
            detailedHtml += `<tr>
              <td>${escapeHtml(r.check)}</td>
              <td>${escapeHtml(r.value)}</td>
              <td>${escapeHtml(r.expected)}</td>
              <td class='${statusClass}'>${r.status}</td>
            </tr>`;
          });
          detailedHtml += "</table>";
        });
        resultsDiv.innerHTML = detailedHtml;

      } catch (error) {
        if (error.name === 'AbortError') {
          showAlert('Processing timed out. The PDF may be too large or complex. Try splitting it into smaller files.', "error");
        } else {
          showAlert(`Failed to process PDF: ${error.message}`, "error");
        }
      } finally {
        checkBtn.disabled = false;
        btnText.textContent = "Validate PDF";
      }
    }

    function showAlert(message, type) {
      const alertDiv = document.getElementById("alert");
      alertDiv.className = type === "error" ? "alert alert-error" : "alert alert-success";
      alertDiv.textContent = message;
    }

    function escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    document.getElementById('file').addEventListener('keypress', function(e) {
      if (e.key === 'Enter') { checkPDF(); }
    });
  </script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Management fee validation using per-property lookup
# ---------------------------------------------------------------------------
def normalize_code(code):
    """Lowercase, strip whitespace, remove common punctuation for fuzzy comparison."""
    return re.sub(r'[\s\-_/\.,]', '', str(code).lower().strip())

def find_property_fee(prop_code):
    """
    Try to find a matching property in PROPERTY_FEES.
    1. Exact match
    2. Case/whitespace-insensitive match
    3. Fuzzy match (ignore dashes, spaces, punctuation)
    Returns the matched fee entry and the matched key, or (None, None).
    """
    # 1. Exact match
    if prop_code in PROPERTY_FEES:
        return PROPERTY_FEES[prop_code], prop_code

    # 2. Normalized match (case + whitespace insensitive)
    normalized_input = normalize_code(prop_code)
    for key, entry in PROPERTY_FEES.items():
        if normalize_code(key) == normalized_input:
            return entry, key

    # 3. Fuzzy match — accept if normalized codes share enough characters
    best_match_key = None
    best_score = 0
    for key in PROPERTY_FEES:
        nk = normalize_code(key)
        ni = normalized_input
        # Simple similarity: length of common prefix + matching chars ratio
        shorter = min(len(nk), len(ni))
        if shorter == 0:
            continue
        matches = sum(a == b for a, b in zip(nk, ni))
        score = matches / max(len(nk), len(ni))
        if score > best_score:
            best_score = score
            best_match_key = key

    if best_score >= 0.85 and best_match_key:
        return PROPERTY_FEES[best_match_key], best_match_key

    return None, None

def validate_management_fee(prop_code, management_fee_dollar_extracted, management_fee_percent_extracted):
    """
    Returns a list of result dicts and updates has_failures / failed_checks_for_summary.
    Returns: (results_list, has_failures, failed_checks)
    """
    results = []
    has_failures = False
    failed_checks = []

    # Look up this property in the fee table
    fee_entry, matched_key = find_property_fee(prop_code)
    
    if matched_key and matched_key != prop_code:
        # Log that we used a fuzzy match
        results.append({
            "check": "Management Fee — Property Lookup",
            "value": f"'{prop_code}' matched to '{matched_key}'",
            "expected": "Exact or close match found",
            "status": "INFO"
        })

    if fee_entry is None:
        # Property not found in lookup file — FAIL
        has_failures = True
        failed_checks.append("Property Not in Fee Lookup File")
        results.append({
            "check": "Management Fee — Property Lookup",
            "value": f"'{prop_code}' not found in property_fees.xlsx",
            "expected": "Property must be listed in property_fees.xlsx",
            "status": "FAIL"
        })
        return results, has_failures, failed_checks

    expected_percent = fee_entry.get("fee_percent")
    expected_dollar  = fee_entry.get("min_dollar_charge")

    # --- Percent check ---
    if expected_percent is not None:
        if management_fee_percent_extracted is not None:
            passes = abs(management_fee_percent_extracted - expected_percent) < 0.001
            status = "PASS" if passes else "FAIL"
            if not passes:
                has_failures = True
                failed_checks.append("Management Fee (%) Mismatch")
            results.append({
                "check": "Management Fee (%) Match",
                "value": f"{management_fee_percent_extracted:.2f}%",
                "expected": f"{expected_percent:.2f}%",
                "status": status
            })
        else:
            has_failures = True
            failed_checks.append("Management Fee (%) Not Found")
            results.append({
                "check": "Management Fee (%) Match",
                "value": "N/A (Not Found)",
                "expected": f"{expected_percent:.2f}%",
                "status": "FAIL"
            })

    # --- Dollar check ---
    if expected_dollar is not None:
        if management_fee_dollar_extracted is not None:
            passes = abs(management_fee_dollar_extracted - expected_dollar) < 0.01
            status = "PASS" if passes else "FAIL"
            if not passes:
                has_failures = True
                failed_checks.append("Management Fee ($) Mismatch")
            results.append({
                "check": "Management Fee ($) Match",
                "value": f"${management_fee_dollar_extracted:,.2f}",
                "expected": f"${expected_dollar:,.2f}",
                "status": status
            })
        else:
            has_failures = True
            failed_checks.append("Management Fee ($) Not Found")
            results.append({
                "check": "Management Fee ($) Match",
                "value": "N/A (Not Found)",
                "expected": f"${expected_dollar:,.2f}",
                "status": "FAIL"
            })

    return results, has_failures, failed_checks


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------
def parse_pdf(file_stream):
    tmp_path = None
    doc = None
    final_property_checks = []
    failing_properties_summary = []

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            file_stream.seek(0)
            tmp.write(file_stream.read())
            tmp.flush()
            tmp_path = tmp.name

        doc = fitz.open(tmp_path)
        property_page_map = {}
        current_property_key = None

        all_pages_text_by_num = {p_num: doc.load_page(p_num).get_text("text") for p_num in range(doc.page_count)}

        for page_num, page_text in all_pages_text_by_num.items():
            if page_num >= CONFIG["MAX_PAGES"]:
                break

            property_header_line = None
            for line in page_text.splitlines():
                if line.strip().startswith("Properties:"):
                    property_header_line = line.strip()
                    break

            if property_header_line:
                try:
                    header_content = property_header_line.replace("Properties:", "").strip()
                    if '-' in header_content:
                        code_part, addr_part = header_content.split("-", 1)
                        code = code_part.strip()
                        addr = addr_part.strip()
                    else:
                        code = header_content
                        addr = "N/A"
                    new_property_key = (code, addr)

                    if new_property_key != current_property_key:
                        current_property_key = new_property_key
                        if current_property_key not in property_page_map:
                            property_page_map[current_property_key] = []
                except ValueError:
                    if current_property_key is None:
                        current_property_key = ("UNKNOWN", "UNKNOWN (Header Parse Error)")
                        if current_property_key not in property_page_map:
                            property_page_map[current_property_key] = []

            if current_property_key:
                property_page_map[current_property_key].append(page_num)
            else:
                if ("UNASSIGNED", "NO_HEADER") not in property_page_map:
                    property_page_map[("UNASSIGNED", "NO_HEADER")] = []
                property_page_map[("UNASSIGNED", "NO_HEADER")].append(page_num)

        for (prop_code, prop_address), relevant_page_nums_for_prop in property_page_map.items():

            cash_in_bank_operating = None
            actual_ending_cash = None
            management_fee_dollar_extracted = None
            management_fee_percent_extracted = None
            prepaid_rent_liability_value = None
            total_negative_past_due_sum = 0.0

            full_property_text_for_lines = "\n".join([all_pages_text_by_num[p_num] for p_num in relevant_page_nums_for_prop])
            lines_for_extraction = full_property_text_for_lines.splitlines()

            standalone_number_pattern = re.compile(r"^\s*([-]?[\d,]+\.?\d{0,2})\s*$")

            for i, line in enumerate(lines_for_extraction):
                stripped_line = line.strip()

                if "Cash in Bank - Operating" == stripped_line and cash_in_bank_operating is None:
                    if i + 1 < len(lines_for_extraction):
                        next_line = lines_for_extraction[i+1].strip()
                        match = standalone_number_pattern.match(next_line)
                        if match:
                            try: cash_in_bank_operating = float(match.group(1).replace(",", ""))
                            except ValueError: pass

                if "Actual Ending Cash" == stripped_line and actual_ending_cash is None:
                    if i + 1 < len(lines_for_extraction):
                        next_line = lines_for_extraction[i+1].strip()
                        match = standalone_number_pattern.match(next_line)
                        if match:
                            try: actual_ending_cash = float(match.group(1).replace(",", ""))
                            except ValueError: pass

            for i, line in enumerate(lines_for_extraction):
                stripped_line = line.strip()

                if stripped_line == "Management Fees" and management_fee_dollar_extracted is None:
                    if i + 1 < len(lines_for_extraction):
                        next_line = lines_for_extraction[i+1].strip()
                        dollar_match = standalone_number_pattern.match(next_line)
                        if dollar_match:
                            try: management_fee_dollar_extracted = float(dollar_match.group(1).replace(",", ""))
                            except ValueError: pass

                            if i + 2 < len(lines_for_extraction):
                                percent_line = lines_for_extraction[i+2].strip()
                                percent_match = standalone_number_pattern.match(percent_line)
                                if percent_match:
                                    try: management_fee_percent_extracted = float(percent_match.group(1).replace(",", ""))
                                    except ValueError: pass
                    break

            for i, line in enumerate(lines_for_extraction):
                stripped_line = line.strip()
                if "Prepaid Rent Liability" in stripped_line and prepaid_rent_liability_value is None:
                    match = re.search(r"Prepaid Rent Liability.*?([-]?[\d,]+\.?\d{0,2})", stripped_line, re.IGNORECASE)
                    if match:
                        try:
                            value = float(match.group(1).replace(",", ""))
                            if value >= 0:
                                prepaid_rent_liability_value = value
                        except ValueError: pass

                    if prepaid_rent_liability_value is None and i + 1 < len(lines_for_extraction):
                        next_line = lines_for_extraction[i+1].strip()
                        match = standalone_number_pattern.match(next_line)
                        if match:
                            try:
                                value = float(match.group(1).replace(",", ""))
                                if value >= 0:
                                    prepaid_rent_liability_value = value
                            except ValueError: pass
                    break

            # Rent Roll Logic
            past_due_col_x0 = -1
            past_due_col_x1 = -1
            header_y_coord = -1
            number_pattern_for_past_due = re.compile(r"([-]?[\d,]+\.?\d{0,2})")
            expected_header_phrases = ["Unit", "Tenant", "Additional Tenants", "Status", "Rent", "Deposit", "Move-in", "Lease From", "Lease To", "Past Due"]

            rent_roll_page_num = -1
            rent_roll_title_y = -1
            rent_word_pattern = re.compile(r"rent", re.IGNORECASE)
            roll_word_pattern = re.compile(r"roll", re.IGNORECASE)

            for p_num in relevant_page_nums_for_prop:
                page = doc.load_page(p_num)
                page_words = page.get_text("words")
                page_words.sort(key=lambda w: (w[1], w[0]))

                last_rent_word = None
                for word_bbox in page_words:
                    word_text = word_bbox[4]

                    if rent_word_pattern.search(word_text):
                        last_rent_word = word_bbox
                    elif roll_word_pattern.search(word_text) and last_rent_word:
                        if abs(word_bbox[1] - last_rent_word[1]) < 5 and (word_bbox[0] - last_rent_word[2]) < 10:
                            rent_roll_page_num = p_num
                            rent_roll_title_y = last_rent_word[1]
                            break
                    else:
                        last_rent_word = None

                if rent_roll_page_num != -1:
                    break

            if rent_roll_page_num != -1:
                all_property_words = doc.load_page(rent_roll_page_num).get_text("words")

                if rent_roll_title_y != -1:
                    all_property_words = [word for word in all_property_words if word[1] > rent_roll_title_y + 30]

                all_property_words.sort(key=lambda w: (w[1], w[0]))

                reconstructed_lines_of_words = []
                line_y_group_tolerance = 1

                if all_property_words:
                    current_line_words_group = []
                    current_line_y_sum = 0
                    current_line_word_count = 0

                    for word in all_property_words:
                        word_y_center = (word[1] + word[3]) / 2

                        if not current_line_words_group:
                            current_line_words_group.append(word)
                            current_line_y_sum += word_y_center
                            current_line_word_count += 1
                        else:
                            current_line_y_avg = current_line_y_sum / current_line_word_count
                            if abs(word_y_center - current_line_y_avg) < line_y_group_tolerance:
                                current_line_words_group.append(word)
                                current_line_y_sum += word_y_center
                                current_line_word_count += 1
                            else:
                                reconstructed_lines_of_words.append(current_line_words_group)
                                current_line_words_group = [word]
                                current_line_y_sum = word_y_center
                                current_line_word_count = 1

                    if current_line_words_group:
                        reconstructed_lines_of_words.append(current_line_words_group)

                for line_idx, current_line_words_for_reco in enumerate(reconstructed_lines_of_words):
                    current_line_words_for_reco.sort(key=lambda w: w[0])

                    y_key = round(current_line_words_for_reco[0][1])
                    full_line_text = " ".join([w[4] for w in current_line_words_for_reco])

                    if header_y_coord == -1:
                        found_all_phrases_in_sequence = True
                        current_search_text = full_line_text
                        past_due_word_bbox_in_header = None

                        for i, phrase in enumerate(expected_header_phrases):
                            phrase_pattern = r'\b' + re.escape(phrase) + r'\b'
                            match = re.search(phrase_pattern, current_search_text, re.IGNORECASE)

                            if not match:
                                found_all_phrases_in_sequence = False
                                break

                            if phrase == "Past Due":
                                _past_word_temp = None
                                _due_word_temp = None
                                for word_bbox in current_line_words_for_reco:
                                    if re.search(r'\bPast\b', word_bbox[4], re.IGNORECASE):
                                        _past_word_temp = word_bbox
                                    elif re.search(r'\bDue\b', word_bbox[4], re.IGNORECASE):
                                        _due_word_temp = word_bbox

                                    if _past_word_temp and _due_word_temp and abs(_due_word_temp[1] - _past_word_temp[1]) < 5 and (_due_word_temp[0] - _past_word_temp[2]) < 10:
                                        past_due_word_bbox_in_header = (_past_word_temp[0], _past_word_temp[1], _due_word_temp[2], _due_word_temp[3])
                                        break
                                    elif re.search(r'\bPast\s*Due\b', word_bbox[4], re.IGNORECASE):
                                        past_due_word_bbox_in_header = word_bbox
                                        break
                                if not past_due_word_bbox_in_header:
                                    found_all_phrases_in_sequence = False
                                    break

                            current_search_text = current_search_text[match.end():]

                        if found_all_phrases_in_sequence and past_due_word_bbox_in_header:
                            header_y_coord = y_key
                            temp_past_due_x0 = past_due_word_bbox_in_header[0]
                            temp_past_due_x1 = past_due_word_bbox_in_header[2]

                            if temp_past_due_x0 != float('inf'):
                                past_due_col_x0 = temp_past_due_x0 - 5
                                past_due_col_x1 = temp_past_due_x1 + 5
                            else:
                                header_y_coord = -1
                                past_due_col_x0 = -1
                                past_due_col_x1 = -1

                    if header_y_coord != -1 and past_due_col_x0 != -1 and past_due_col_x1 != -1:
                        if y_key == header_y_coord:
                            continue

                        extracted_words_in_column = []
                        for word in current_line_words_for_reco:
                            x0, y0, x1, y1, text_content, *_ = word
                            if (x0 < past_due_col_x1 + 5 and x1 > past_due_col_x0 - 5):
                                extracted_words_in_column.append(text_content)

                        column_content = " ".join(extracted_words_in_column).strip()

                        is_grand_total_line = bool(re.search(r'\bGrand\s*Total\b', full_line_text, re.IGNORECASE))
                        is_long_separator_line = bool(re.match(r"^\s*[-=]{10,}\s*$", full_line_text))

                        if (is_grand_total_line and y_key > header_y_coord) or \
                           (is_long_separator_line and y_key > header_y_coord + 10 and line_idx > 5):
                            break

                        if y_key > header_y_coord:
                            if column_content:
                                match = number_pattern_for_past_due.search(column_content)
                                if match:
                                    value_str = match.group(1).replace(",", "").replace("$", "").strip()
                                    try:
                                        numeric_value = float(value_str)
                                        is_summary_line = bool(re.search(r'\b(Total|Summary|Grand Total|Subtotal|Current Due|Current\s*Activity|Balance|Activity|Actual)\b', full_line_text, re.IGNORECASE)) or \
                                                          bool(re.search(r'\d{1,3}(?:[,\.]\d{3})*(?:[,\.]\d+)?\s*%', full_line_text, re.IGNORECASE))
                                        is_walnut_exclusion = bool(re.search(r'walnut\d+ - \d+', full_line_text, re.IGNORECASE))

                                        if numeric_value < 0 and not is_summary_line and not is_walnut_exclusion:
                                            total_negative_past_due_sum += numeric_value
                                    except ValueError:
                                        pass

            # -------------------------------------------------------------------
            # Build results
            # -------------------------------------------------------------------
            property_results = []
            has_failures = False
            failed_checks_for_summary = []

            # Cash in Bank - Operating
            if cash_in_bank_operating is not None:
                status = "PASS" if cash_in_bank_operating > 0 else "FAIL"
                if status == "FAIL":
                    has_failures = True
                    failed_checks_for_summary.append("Cash in Bank - Operating Positive")
                property_results.append({
                    "check": "Cash in Bank - Operating Positive",
                    "value": f"${cash_in_bank_operating:,.2f}",
                    "expected": "> $0",
                    "status": status
                })
            else:
                property_results.append({
                    "check": "Cash in Bank - Operating Positive",
                    "value": "N/A (Not Found)",
                    "expected": "> $0",
                    "status": "INFO"
                })

            # Actual Ending Cash
            if actual_ending_cash is not None:
                status = "PASS" if actual_ending_cash > 0 else "FAIL"
                if status == "FAIL":
                    has_failures = True
                    failed_checks_for_summary.append("Actual Ending Cash Positive")
                property_results.append({
                    "check": "Actual Ending Cash Positive",
                    "value": f"${actual_ending_cash:,.2f}",
                    "expected": "> $0",
                    "status": status
                })
            else:
                property_results.append({
                    "check": "Actual Ending Cash Positive",
                    "value": "N/A (Not Found)",
                    "expected": "> $0",
                    "status": "INFO"
                })

            # Management Fee — per-property lookup
            fee_results, fee_has_failures, fee_failed_checks = validate_management_fee(
                prop_code, management_fee_dollar_extracted, management_fee_percent_extracted
            )
            property_results.extend(fee_results)
            if fee_has_failures:
                has_failures = True
                failed_checks_for_summary.extend(fee_failed_checks)

            # Prepaid Rent Liability
            if prepaid_rent_liability_value is not None:
                status = "PASS" if prepaid_rent_liability_value >= 0 else "FAIL"
                if status == "FAIL":
                    has_failures = True
                    failed_checks_for_summary.append("Prepaid Rent Liability Non-Negative")
                property_results.append({
                    "check": "Prepaid Rent Liability Non-Negative",
                    "value": f"${prepaid_rent_liability_value:,.2f}",
                    "expected": ">= $0",
                    "status": status
                })
            else:
                property_results.append({
                    "check": "Prepaid Rent Liability Non-Negative",
                    "value": "N/A (Not Found)",
                    "expected": ">= $0",
                    "status": "INFO"
                })

            # Sum of Negative Past Due vs Prepaid Rent Liability
            expected_status_text = "N/A (Calculated Sum)"
            match_status_for_display = "INFO"
            display_value = "N/A (No negative values found)"
            if total_negative_past_due_sum < 0:
                display_value = f"${total_negative_past_due_sum:,.2f}"

            if total_negative_past_due_sum < 0 and prepaid_rent_liability_value is not None:
                epsilon = 0.001
                if abs(abs(total_negative_past_due_sum) - prepaid_rent_liability_value) < epsilon:
                    expected_status_text = "Match"
                    match_status_for_display = "PASS"
                else:
                    expected_status_text = f"No Match (Expected {prepaid_rent_liability_value:,.2f})"
                    match_status_for_display = "FAIL"
                    has_failures = True
                    failed_checks_for_summary.append("Sum of Negative Past Due (Rent Roll)")
            elif total_negative_past_due_sum == 0 and prepaid_rent_liability_value == 0:
                expected_status_text = "Match (No Negative Past Due, No Prepaid Liability)"
                match_status_for_display = "PASS"
            elif total_negative_past_due_sum >= 0:
                if prepaid_rent_liability_value is not None and prepaid_rent_liability_value > 0:
                    expected_status_text = f"No Match (Expected {prepaid_rent_liability_value:,.2f}, no negative past due found)"
                    match_status_for_display = "FAIL"
                    has_failures = True
                    failed_checks_for_summary.append("Sum of Negative Past Due (Rent Roll)")
                else:
                    expected_status_text = "N/A (No Negative Past Due to Compare)"
                    match_status_for_display = "INFO"
            elif prepaid_rent_liability_value is None:
                expected_status_text = "N/A (Prepaid Liability Not Found for Comparison)"
                match_status_for_display = "INFO"

            property_results.append({
                "check": "Sum of Negative Past Due (Rent Roll)",
                "value": display_value,
                "expected": expected_status_text,
                "status": match_status_for_display
            })

            final_property_checks.append({
                "property": f"{prop_code} - {prop_address}",
                "results": property_results
            })

            if has_failures:
                failing_properties_summary.append({
                    "property": f"{prop_code} - {prop_address}",
                    "failed_checks": failed_checks_for_summary
                })

    finally:
        if doc:
            doc.close()
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

    return {"detailed_checks": final_property_checks, "failing_summary": failing_properties_summary}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    from flask import render_template_string
    return render_template_string(
        HTML_TEMPLATE,
        fees_error=FEES_FILE_ERROR,
        fees_count=len(PROPERTY_FEES)
    )

@app.route('/check_pdf', methods=['POST'])
def check_pdf():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if file and file.filename.endswith('.pdf'):
        try:
            results = parse_pdf(file.stream)
            if not results:
                return jsonify({'error': 'No properties found or parsed in the PDF.'}), 400
            return jsonify(results)
        except MemoryError:
            return jsonify({'error': 'PDF file is too large to process. Try splitting it into smaller files.'}), 413
        except TimeoutError:
            return jsonify({'error': 'Processing timed out. The PDF is too complex. Try splitting it into smaller files.'}), 504
        except Exception as e:
            error_msg = str(e)
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "..."
            return jsonify({'error': f'Failed to process PDF: {error_msg}'}), 500
    else:
        return jsonify({'error': 'Invalid file type. Please upload a PDF.'}), 400


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port

def open_browser(port):
    import time
    time.sleep(1.5)
    webbrowser.open(f'http://127.0.0.1:{port}')

if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except:
        pass

    port = find_free_port()
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    print(f"""
╔══════════════════════════════════════════════════════════╗
║         🏠 PDF Property Validator is Running            ║
║  Your browser should open automatically.                ║
║  If not, open: http://127.0.0.1:{port}                ║
║  Press Ctrl+C to stop the application                   ║
╚══════════════════════════════════════════════════════════╝
    """)

    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.WARNING)

    from werkzeug.serving import run_simple
    run_simple('127.0.0.1', port, app, use_reloader=False, use_debugger=False, threaded=True)
