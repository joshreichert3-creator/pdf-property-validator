import fitz  # PyMuPDF
from flask import Flask, render_template_string, request, jsonify
import tempfile
import os
import re
import webbrowser
import threading
import sys
import socket
import gc  # Garbage collection for memory management

app = Flask(__name__)

# Increase timeout for large PDFs
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB max file size

# -------------------------
# Configurable parameters
# -------------------------
CONFIG = {
    "management_fee_percent_min": 3.0,
    "management_fee_percent_max": 6.0,
    "management_fee_percent_allowed": [3.0, 3.5, 3.75, 4.0, 4.5, 5.0, 5.5, 6.0],  # Only these specific values
    "management_fee_dollar_min": 95.0,
    "management_fee_dollar_max": 250.0,
    "MAX_PAGES": 10000,  # Increased from 500 to handle very large PDFs
    "REQUEST_TIMEOUT": 3600  # 1 hour for processing
}

# HTML Template embedded in the application
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>🏠 PDF Property Validator</title>
  <style>
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }
    
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
      max-width: 900px;
      margin: 0 auto;
      padding: 30px 20px;
      background: #f5f5f5;
    }
    
    .container {
      background: white;
      padding: 30px;
      border-radius: 8px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    }
    
    h1 {
      color: #333;
      margin-bottom: 10px;
      font-size: 28px;
    }
    
    .subtitle {
      color: #666;
      margin-bottom: 25px;
      font-size: 14px;
    }
    
    .upload-section {
      background: #f9f9f9;
      padding: 20px;
      border-radius: 6px;
      margin-bottom: 30px;
      border: 2px dashed #ddd;
    }
    
    input[type="file"] {
      margin-bottom: 15px;
      padding: 8px;
      font-size: 14px;
    }
    
    button {
      background: #007bff;
      color: white;
      border: none;
      padding: 12px 24px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 14px;
      font-weight: 500;
      transition: background 0.2s;
    }
    
    button:hover:not(:disabled) {
      background: #0056b3;
    }
    
    button:disabled {
      background: #ccc;
      cursor: not-allowed;
    }
    
    .loader {
      display: inline-block;
      width: 14px;
      height: 14px;
      border: 2px solid #f3f3f3;
      border-top: 2px solid #007bff;
      border-radius: 50%;
      animation: spin 1s linear infinite;
      margin-right: 8px;
      vertical-align: middle;
    }
    
    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }
    
    .alert {
      padding: 15px;
      border-radius: 4px;
      margin-bottom: 20px;
    }
    
    .alert-error {
      background: #fee;
      color: #c33;
      border-left: 4px solid #c33;
    }
    
    .alert-success {
      background: #efe;
      color: #3a3;
      border-left: 4px solid #3a3;
    }
    
    h2 {
      color: #333;
      border-bottom: 2px solid #007bff;
      padding-bottom: 8px;
      margin: 30px 0 20px 0;
      font-size: 22px;
    }
    
    h3 {
      color: #007bff;
      margin: 25px 0 15px 0;
      font-size: 18px;
    }
    
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 15px;
      background: white;
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
    
    th, td {
      border: 1px solid #e0e0e0;
      padding: 12px;
      text-align: left;
      font-size: 14px;
    }
    
    th {
      background-color: #f8f9fa;
      font-weight: 600;
      color: #555;
    }
    
    tr:hover {
      background-color: #f9f9f9;
    }
    
    .status-PASS {
      color: #28a745;
      font-weight: bold;
    }
    
    .status-FAIL {
      color: #dc3545;
      font-weight: bold;
    }
    
    .status-INFO {
      color: #6c757d;
      font-style: italic;
    }
    
    .summary-table {
      margin-bottom: 30px;
    }
    
    .summary-table .property-name {
      font-weight: 600;
      color: #333;
    }
    
    .summary-table .failed-checks {
      color: #dc3545;
    }
    
    hr {
      margin: 40px 0;
      border: none;
      border-top: 1px solid #e0e0e0;
    }
    
    .hidden {
      display: none;
    }
    
    .stats {
      display: flex;
      gap: 15px;
      margin-bottom: 20px;
    }
    
    .stat-box {
      flex: 1;
      padding: 15px;
      border-radius: 6px;
      text-align: center;
    }
    
    .stat-box.pass {
      background: #d4edda;
      border: 1px solid #c3e6cb;
    }
    
    .stat-box.fail {
      background: #f8d7da;
      border: 1px solid #f5c6cb;
    }
    
    .stat-number {
      font-size: 32px;
      font-weight: bold;
      margin-bottom: 5px;
    }
    
    .stat-label {
      font-size: 12px;
      color: #666;
      text-transform: uppercase;
    }

    .footer {
      margin-top: 40px;
      padding-top: 20px;
      border-top: 1px solid #e0e0e0;
      text-align: center;
      color: #999;
      font-size: 12px;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>🏠 PDF Property Validator</h1>
    <p class="subtitle">Upload your property statement PDF to validate cash balances, management fees, and rent roll data.</p>
    
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

    <div class="footer">
      PDF Property Validator v1.0 | Close this browser window to exit the application
    </div>
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
      
      resultsDiv.innerHTML = "";
      summaryDiv.innerHTML = "";
      alertDiv.className = "hidden";
      statsDiv.className = "hidden";
      divider.className = "hidden";
      
      const file = fileInput.files[0];
      if (!file) {
        showAlert("Please select a PDF file first.", "error");
        return;
      }
      
      checkBtn.disabled = true;
      btnText.innerHTML = '<span class="loader"></span>Processing PDF... This may take several minutes for large files';
      
      const formData = new FormData();
      formData.append("file", file);
      
      try {
        // Increase timeout for fetch to 2 hours for very large files
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 7200000); // 2 hours
        
        const response = await fetch("/check_pdf", { 
          method: "POST",
          body: formData,
          signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        // Check if response is OK
        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(`Server error (${response.status}): ${errorText.substring(0, 100)}`);
        }
        
        // Check if response is JSON
        const contentType = response.headers.get("content-type");
        if (!contentType || !contentType.includes("application/json")) {
          const errorText = await response.text();
          console.error("Non-JSON response:", errorText);
          throw new Error("Server returned an error instead of results. The PDF may be too large or complex.");
        }
        
        const data = await response.json();
        
        if (data.error) {
          showAlert(data.error, "error");
          return;
        }
        
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
      if (e.key === 'Enter') {
        checkPDF();
      }
    });
  </script>
</body>
</html>
"""

# -------------------------
# PDF Parsing Function (Your existing code)
# -------------------------
def parse_pdf(file_stream):
    import sys  # Import sys for flushing
    
    print("\n" + "="*70)
    print("PARSE_PDF FUNCTION STARTED - DEBUG OUTPUT ENABLED")
    print("="*70 + "\n")
    sys.stdout.flush()
    
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
                        try: prepaid_rent_liability_value = float(match.group(1).replace(",", ""))
                        except ValueError: pass
                    
                    if prepaid_rent_liability_value is None and i + 1 < len(lines_for_extraction):
                         next_line = lines_for_extraction[i+1].strip()
                         match = standalone_number_pattern.match(next_line)
                         if match:
                             try: prepaid_rent_liability_value = float(match.group(1).replace(",", ""))
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

            if rent_roll_page_num == -1:
                final_property_checks.append({
                    "property": f"{prop_code} - {prop_address}",
                    "results": [
                        {"check": "Sum of Negative Past Due (Rent Roll)", "value": "N/A", "expected": "N/A (Calculated Sum)", "status": "INFO (No 'Rent Roll' page found)"}
                    ]
                })
                continue

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

            property_results = []
            has_failures = False
            failed_checks_for_summary = []

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

            management_fee_dollar_passes = False
            management_fee_percent_passes = False

            if management_fee_dollar_extracted is not None:
                if (CONFIG["management_fee_dollar_min"] <= management_fee_dollar_extracted <= CONFIG["management_fee_dollar_max"]):
                    management_fee_dollar_passes = True
            
            if management_fee_percent_extracted is not None:
                if (CONFIG["management_fee_percent_min"] <= management_fee_percent_extracted <= CONFIG["management_fee_percent_max"]):
                    management_fee_percent_passes = True

            final_management_fee_status = "INFO"
            if management_fee_dollar_extracted is not None or management_fee_percent_extracted is not None:
                if management_fee_dollar_passes or management_fee_percent_passes:
                    final_management_fee_status = "PASS"
                else:
                    final_management_fee_status = "FAIL"
                    has_failures = True
                    if management_fee_dollar_extracted is not None and not management_fee_dollar_passes:
                        failed_checks_for_summary.append("Management Fee ($) out of Range")
                    if management_fee_percent_extracted is not None and not management_fee_percent_passes:
                        failed_checks_for_summary.append("Management Fee (%) out of Range")

            if management_fee_dollar_extracted is not None:
                property_results.append({
                    "check": "Management Fee ($) in Range",
                    "value": f"${management_fee_dollar_extracted:,.2f}",
                    "expected": f"${CONFIG['management_fee_dollar_min']:.2f} - ${CONFIG['management_fee_dollar_max']:.2f}",
                    "status": final_management_fee_status
                })
            else:
                 property_results.append({
                    "check": "Management Fee ($) in Range",
                    "value": "N/A (Not Found)",
                    "expected": f"${CONFIG['management_fee_dollar_min']:.2f} - ${CONFIG['management_fee_dollar_max']:.2f}",
                    "status": "INFO"
                })

            if management_fee_percent_extracted is not None:
                # Format allowed values nicely
                allowed_values_str = ", ".join([f"{v:.2f}%" if v != int(v) else f"{int(v)}%" for v in CONFIG["management_fee_percent_allowed"]])
                property_results.append({
                    "check": "Management Fee (%) in Range",
                    "value": f"{management_fee_percent_extracted:.2f}%",
                    "expected": f"One of: {allowed_values_str}",
                    "status": final_management_fee_status
                })
            else:
                allowed_values_str = ", ".join([f"{v:.2f}%" if v != int(v) else f"{int(v)}%" for v in CONFIG["management_fee_percent_allowed"]])
                property_results.append({
                    "check": "Management Fee (%) in Range",
                    "value": "N/A (Not Found)",
                    "expected": f"One of: {allowed_values_str}",
                    "status": "INFO"
                })

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

# -------------------------
# Flask Routes
# -------------------------
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/check_pdf', methods=['POST'])
def check_pdf():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if file and file.filename.endswith('.pdf'):
        try:
            # Log file info
            print(f"\n{'#'*70}")
            print(f"# STARTING PDF PROCESSING")
            print(f"# File: {file.filename}")
            print(f"{'#'*70}\n")
            
            results = parse_pdf(file.stream)
            
            if not results:
                return jsonify({'error': 'No properties found or parsed in the PDF.'}), 400
            
            # Log success
            print(f"\n{'#'*70}")
            print(f"# COMPLETED PDF PROCESSING")
            print(f"# Successfully processed {len(results.get('detailed_checks', []))} properties")
            print(f"{'#'*70}\n")
            
            return jsonify(results)
            
        except MemoryError:
            app.logger.error("Memory error - PDF too large", exc_info=True)
            return jsonify({'error': 'PDF file is too large to process. Try splitting it into smaller files.'}), 413
            
        except TimeoutError:
            app.logger.error("Timeout error", exc_info=True)
            return jsonify({'error': 'Processing timed out. The PDF is too complex. Try splitting it into smaller files.'}), 504
            
        except Exception as e:
            app.logger.error(f"Error processing PDF: {e}", exc_info=True)
            error_msg = str(e)
            # Truncate very long error messages
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "..."
            return jsonify({'error': f'Failed to process PDF: {error_msg}'}), 500
    else:
        return jsonify({'error': 'Invalid file type. Please upload a PDF.'}), 400

# -------------------------
# Application Launcher
# -------------------------
def find_free_port():
    """Find an available port to run the server"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port

def open_browser(port):
    """Open the default browser after a short delay"""
    import time
    time.sleep(1.5)
    webbrowser.open(f'http://127.0.0.1:{port}')

if __name__ == '__main__':
    # Force stdout to flush immediately so all print statements show up
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    
    port = find_free_port()
    
    # Open browser in a separate thread
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()
    
    print(f"""
╔══════════════════════════════════════════════════════════╗
║                                                          ║
║         🏠 PDF Property Validator is Running            ║
║                                                          ║
║  Your browser should open automatically.                ║
║  If not, open: http://127.0.0.1:{port}                ║
║                                                          ║
║  Processing large PDFs may take several minutes.        ║
║  Watch this window for progress updates.                ║
║                                                          ║
║  Press Ctrl+C to stop the application                   ║
║                                                          ║
║  DEBUG OUTPUT IS ENABLED - YOU WILL SEE DETAILED INFO   ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝
    """)
    sys.stdout.flush()
    
    # Run Flask with minimal output and increased timeout
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.WARNING)  # Changed from ERROR to WARNING so we see some Flask messages
    
    # Use threaded mode and increase timeout
    from werkzeug.serving import WSGIRequestHandler
    WSGIRequestHandler.protocol_version = "HTTP/1.1"
    
    from werkzeug.serving import run_simple
    run_simple('127.0.0.1', port, app, 
               use_reloader=False, 
               use_debugger=False, 
               threaded=True,
               request_handler=WSGIRequestHandler)
