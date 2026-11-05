#!/usr/bin/env python3
"""Phase 19C — generate 8 new ATS sandbox fixtures (platform-authentic DOM).

Each fixture covers the Phase 19C checklist: basic form, required fields,
optional fields, dropdowns, radio groups, checkboxes, resume upload,
validation errors, multi-step navigation (where the platform is multi-step),
and a confirmation page. Fixtures are LOCAL files only — no external site is
touched and no real application can ever be submitted from them.
"""
from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "e2e" / "fixtures"

BASE_CSS = """
    body { font-family: system-ui, sans-serif; max-width: 700px; margin: 30px auto;
           padding: 0 16px; color: #222; }
    label { display: block; font-weight: 600; margin-top: 14px; font-size: 14px; }
    input, select, textarea { width: 100%; padding: 8px; margin-top: 4px;
           box-sizing: border-box; border: 1px solid #cfd7e0; border-radius: 4px; }
    button, input[type='submit'] { margin-top: 20px; padding: 10px 20px; color: #fff;
           border: 0; border-radius: 4px; cursor: pointer; font-weight: 600; }
    .error, [role='alert'] { color: #c0392b; font-size: 13px; display: none; }
    .step { border: 1px solid #e1e6eb; border-radius: 8px; padding: 20px; }
    .confirmation { text-align: center; padding: 60px 20px; display: none; }
    .confirmation h2 { color: #0a7f24; }
"""

JS_CORE = """
  function show(selector) { var el = document.querySelector(selector); if (el) el.style.display = 'block'; }
  function hide(selector) { var el = document.querySelector(selector); if (el) el.style.display = 'none'; }
  function validateRequired(ids) {
    var ok = true;
    ids.forEach(function(id) {
      var el = document.getElementById(id);
      if (el && !el.value) { ok = false; show('#' + id + '_error'); el.setAttribute('aria-invalid', 'true'); }
    });
    return ok;
  }
"""

# Shared field blocks reused across fixtures with platform-specific naming.
IDENTITY = """
      <label for="first_name">First Name <span style="color:#c0392b">*</span></label>
      <input id="first_name" name="first_name" type="text" required />
      <div class="error" id="first_name_error" role="alert">First Name is required</div>
      <label for="last_name">Last Name <span style="color:#c0392b">*</span></label>
      <input id="last_name" name="last_name" type="text" required />
      <div class="error" id="last_name_error" role="alert">Last Name is required</div>
      <label for="email">Email Address <span style="color:#c0392b">*</span></label>
      <input id="email" name="email" type="email" required />
      <div class="error" id="email_error" role="alert">A valid email is required</div>
      <label for="phone">Phone (optional)</label>
      <input id="phone" name="phone" type="tel" />
"""

DROPDOWN_RADIO = """
      <label for="notice">What is your notice period? <span style="color:#c0392b">*</span></label>
      <select id="notice" name="notice_period" required>
        <option value="">Select...</option>
        <option value="immediate">Immediate</option>
        <option value="15 days">15 days</option>
        <option value="1 month">1 month</option>
        <option value="2 months">2 months</option>
      </select>
      <label>Are you willing to relocate? <span style="color:#c0392b">*</span></label>
      <input type="radio" id="reloc_yes" name="relocation" value="yes" /> <label for="reloc_yes" style="display:inline">Yes</label>
      <input type="radio" id="reloc_no" name="relocation" value="no" /> <label for="reloc_no" style="display:inline">No</label>
"""

CHECKBOX_TERMS = """
      <label style="font-weight:400">
        <input type="checkbox" id="consent" name="data_consent" style="width:auto" />
        I consent to the processing of my data for this application <span style="color:#c0392b">*</span>
      </label>
"""

UPLOAD = """
      <label for="resume">Resume / CV <span style="color:#c0392b">*</span></label>
      <input id="resume" name="resume" type="file" accept=".pdf,.doc,.docx" required />
      <div class="error" id="resume_error" role="alert">Resume file is required</div>
"""


def page(title: str, body: str, script: str, extra_css: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <style>{BASE_CSS}{extra_css}</style>
</head>
<body>
{body}
  <script>{JS_CORE}{script}</script>
</body>
</html>
"""


# ---------------------------------------------------------------- iCIMS (legacy DOM, real input[type=submit], tables)

ICIMS = page(
    "iCIMS Sandbox - Software Engineer I",
    """
  <h1>iCIMS Careers Portal (sandbox)</h1>
  <p>Software Engineer I - Sandbox Technologies India Pvt Ltd (Bengaluru)</p>
  <form id="icims_form" class="iCIMS_JobPage" novalidate>
    <table class="iCIMS_Table" role="presentation" width="100%">
      <tr><td>
""" + IDENTITY + UPLOAD + DROPDOWN_RADIO + CHECKBOX_TERMS + """
      </td></tr>
      <tr><td>
        <input type="submit" id="icims_submit" value="Submit Application" />
        <div class="iCIMS_ErrorMsg" id="form_error" role="alert">Please correct the errors above.</div>
      </td></tr>
    </table>
  </form>
  <div class="confirmation" id="confirmation">
    <h2>Application submitted successfully</h2>
    <p>Your application reference number is <strong>ICIMS-SBX-2027-881</strong>.</p>
  </div>
""",
    """
  document.getElementById('icims_submit').addEventListener('click', function(ev) {
    ev.preventDefault();
    if (!validateRequired(['first_name', 'last_name', 'email'])) return;
    if (!document.getElementById('resume').files.length) { show('#resume_error'); return; }
    hide('#icims_form'); show('#confirmation');
  });
""")

# ---------------------------------------------------------------- Taleo (legacy, many dropdowns)

TALEO = page(
    "Taleo Sandbox - Associate Software Engineer",
    """
  <h1>Taleo Candidate Gateway (sandbox)</h1>
  <p>Associate Software Engineer - Sandbox Global Services (Chennai)</p>
  <form id="taleo_form" class="step" novalidate>
    <div id="step1" class="step">
""" + IDENTITY + """
      <label for="degree">Highest Degree <span style="color:#c0392b">*</span></label>
      <select id="degree" name="education_level" required>
        <option value="">-- Select degree --</option>
        <option value="btech">B.Tech / B.E.</option>
        <option value="mca">MCA</option>
        <option value="bsc">B.Sc</option>
      </select>
      <div class="error" id="degree_error" role="alert">Degree is required</div>
      <input type="button" id="taleo_next" value="Next" style="background:#0e6eb8" />
    </div>
    <div id="step2" class="step" style="display:none">
""" + UPLOAD + DROPDOWN_RADIO + CHECKBOX_TERMS + """
      <input type="submit" id="submit" value="Submit" style="background:#0a7f24" />
    </div>
    <div id="errorMessages" class="error" role="alert">Some fields need your attention.</div>
  </form>
  <div class="confirmation" id="confirmation">
    <h2>Thank you for applying!</h2>
    <p>Requisition TEO-SBX-4412 received. A recruiter will contact you.</p>
  </div>
""",
    """
  document.getElementById('taleo_next').addEventListener('click', function() {
    if (!validateRequired(['first_name', 'last_name', 'email'])) { show('#errorMessages'); return; }
    var d = document.getElementById('degree');
    if (!d.value) { show('#degree_error'); show('#errorMessages'); return; }
    hide('#step1'); show('#step2');
  });
  document.getElementById('submit').addEventListener('click', function(ev) {
    ev.preventDefault();
    if (!document.getElementById('resume').files.length) { show('#resume_error'); return; }
    hide('#taleo_form'); show('#confirmation');
  });
""")

# ---------------------------------------------------------------- Darwinbox (India-native, auth-portal style)

DARWINBOX = page(
    "Darwinbox Sandbox - Graduate Engineer Trainee",
    """
  <h1>Darwinbox Talent Portal (sandbox)</h1>
  <p>Graduate Engineer Trainee - Sandbox Industries Ltd (Hyderabad)</p>
  <form data-db-form="application" class="dbx-apply-form" novalidate>
""" + IDENTITY + """
    <label for="college">College / University <span style="color:#c0392b">*</span></label>
    <input id="college" name="college" type="text" required />
    <div class="error" id="college_error" role="alert">College is required</div>
    <label for="grad_year">Graduation Year <span style="color:#c0392b">*</span></label>
    <select id="grad_year" name="graduation_year" required>
      <option value="">Select year</option>
      <option value="2025">2025</option>
      <option value="2026">2026</option>
      <option value="2027">2027</option>
    </select>
    <div class="error" id="grad_year_error" role="alert">Graduation year is required</div>
""" + UPLOAD + DROPDOWN_RADIO + CHECKBOX_TERMS + """
    <button type="submit" id="dbx_submit" style="background:#7b2ff7">Submit Application</button>
  </form>
  <div class="confirmation" id="confirmation">
    <h2>Application received!</h2>
    <p>Application ID DBX-SBX-77881. Track it from your Darwinbox profile.</p>
  </div>
""",
    """
  document.getElementById('dbx_submit').addEventListener('click', function(ev) {
    ev.preventDefault();
    if (!validateRequired(['first_name', 'last_name', 'email', 'college', 'grad_year'])) return;
    if (!document.getElementById('resume').files.length) { show('#resume_error'); return; }
    hide('[data-db-form]'); show('#confirmation');
  });
""")

# ---------------------------------------------------------------- Zoho Recruit (simple form, India SMBs)

ZOHO = page(
    "Zoho Recruit Sandbox - Python Developer (Fresher)",
    """
  <h1>Zoho Recruit Career Site (sandbox)</h1>
  <p>Python Developer (Fresher) - Sandbox Software Solutions (Coimbatore)</p>
  <form id="zoho_form" class="zc-formbody" novalidate>
    <div class="zcformbody">
""" + IDENTITY + UPLOAD + DROPDOWN_RADIO + """
      <label for="expected">Expected CTC (LPA) (optional)</label>
      <input id="expected" name="expected_ctc" type="number" min="0" step="0.5" />
""" + CHECKBOX_TERMS + """
    </div>
    <button type="submit" id="zoho_submit" style="background:#e42527">Submit</button>
  </form>
  <div class="confirmation" id="confirmation">
    <h2>Applied successfully</h2>
    <p>Thanks for applying to Sandbox Software Solutions. Ref: ZR-SBX-10293.</p>
  </div>
""",
    """
  document.getElementById('zoho_submit').addEventListener('click', function(ev) {
    ev.preventDefault();
    if (!validateRequired(['first_name', 'last_name', 'email'])) return;
    if (!document.getElementById('resume').files.length) { show('#resume_error'); return; }
    hide('#zoho_form'); show('#confirmation');
  });
""")

# ---------------------------------------------------------------- SuccessFactors (long multi-step)

SUCCESSFACTORS = page(
    "SAP SuccessFactors Sandbox - Software Engineer, Graduate Program",
    """
  <h1>SAP SuccessFactors Career Site (sandbox)</h1>
  <p>Software Engineer, Graduate Program - Sandbox SE (Pune)</p>
  <form id="sf_form" data-sap-ui="applicationForm" class="step" novalidate>
    <div id="step1" class="step">
      <h3>Step 1 of 3 - Personal Information</h3>
""" + IDENTITY + """
      <button type="button" id="sf_next1" style="background:#0a6ed1">Next</button>
    </div>
    <div id="step2" class="step" style="display:none">
      <h3>Step 2 of 3 - Education</h3>
      <label for="degree">Degree Program <span style="color:#c0392b">*</span></label>
      <select id="degree" name="degree" required>
        <option value="">Select...</option>
        <option value="btech-cse">B.Tech - Computer Science</option>
        <option value="btech-it">B.Tech - Information Technology</option>
        <option value="other">Other</option>
      </select>
      <div class="error" id="degree_error" role="alert">Degree is required</div>
      <label for="grad_year">Expected Graduation <span style="color:#c0392b">*</span></label>
      <select id="grad_year" name="grad_year" required>
        <option value="">Select...</option>
        <option value="2026">2026</option>
        <option value="2027">2027</option>
      </select>
      <button type="button" id="sf_next2" style="background:#0a6ed1">Next</button>
    </div>
    <div id="step3" class="step" style="display:none">
      <h3>Step 3 of 3 - Documents</h3>
""" + UPLOAD + DROPDOWN_RADIO + CHECKBOX_TERMS + """
      <button type="submit" id="sf_submit" style="background:#0a7f24">Submit Application</button>
    </div>
    <div class="sapMMsgStrip error" id="sf_error" role="alert">Complete all required fields to continue.</div>
  </form>
  <div class="confirmation" id="confirmation">
    <h2>Application submitted</h2>
    <p>Your application number is SF-SBX-100293. You will receive an email confirmation.</p>
  </div>
""",
    """
  document.getElementById('sf_next1').addEventListener('click', function() {
    if (!validateRequired(['first_name', 'last_name', 'email'])) { show('#sf_error'); return; }
    hide('#step1'); show('#step2');
  });
  document.getElementById('sf_next2').addEventListener('click', function() {
    if (!document.getElementById('degree').value) { show('#degree_error'); show('#sf_error'); return; }
    if (!document.getElementById('grad_year').value) { show('#grad_year_error'); show('#sf_error'); return; }
    hide('#step2'); show('#step3');
  });
  document.getElementById('sf_submit').addEventListener('click', function(ev) {
    ev.preventDefault();
    if (!document.getElementById('resume').files.length) { show('#resume_error'); return; }
    hide('#sf_form'); show('#confirmation');
  });
""")

# ---------------------------------------------------------------- Oracle Recruiting Cloud (Workday-like but distinct markers)

ORACLE = page(
    "Oracle Recruiting Cloud Sandbox - Associate Developer",
    """
  <h1>Oracle Recruiting Cloud (sandbox)</h1>
  <p>Associate Developer, 2027 Graduate Program - Sandbox Cloud India (Bengaluru)</p>
  <form id="orc_form" data-oracle-candidate="applyFlow" class="oj-form step" novalidate>
    <div id="step1" class="step">
      <h3>Job Application - Step 1</h3>
""" + IDENTITY + """
      <label for="country">Country of Residence <span style="color:#c0392b">*</span></label>
      <select id="country" name="country" data-oci-country="residence" required>
        <option value="">Select country</option>
        <option value="India">India</option>
        <option value="Other">Other</option>
      </select>
      <div class="error" id="country_error" role="alert">Country is required</div>
      <button type="button" id="orc_next" data-component-id="NextButton"
              style="background:#c74634">Next</button>
    </div>
    <div id="step2" class="step" style="display:none">
      <h3>Job Application - Step 2</h3>
""" + UPLOAD + DROPDOWN_RADIO + CHECKBOX_TERMS + """
      <button type="submit" id="orc_submit" style="background:#0a7f24">Submit</button>
    </div>
    <div class="p_OraError error" id="orc_error" role="alert">Resolve the highlighted fields.</div>
  </form>
  <div class="confirmation" id="confirmation">
    <h2>Application submitted successfully</h2>
    <p>Submission confirmation ORC-SBX-55210. Check your email for details.</p>
  </div>
""",
    """
  document.getElementById('orc_next').addEventListener('click', function() {
    if (!validateRequired(['first_name', 'last_name', 'email', 'country'])) { show('#orc_error'); return; }
    hide('#step1'); show('#step2');
  });
  document.getElementById('orc_submit').addEventListener('click', function(ev) {
    ev.preventDefault();
    if (!document.getElementById('resume').files.length) { show('#resume_error'); return; }
    hide('#orc_form'); show('#confirmation');
  });
""")

# ---------------------------------------------------------------- Eightfold (resume-first flow)

EIGHTFOLD = page(
    "Eightfold AI Sandbox - ML Engineer, Early Career",
    """
  <h1>Eightfold AI Career Portal (sandbox)</h1>
  <p>ML Engineer, Early Career - Sandbox AI Labs (Remote, India)</p>
  <div id="ef_upload_step" class="step">
    <h3>Start with your resume</h3>
    <p>Upload your resume and we'll fill in your details.</p>
""" + UPLOAD + """
    <button type="button" id="ef_continue" style="background:#5b21b6">Continue</button>
  </div>
  <form id="ef_form" class="ef-application-form" style="display:none" novalidate>
    <h3>Review your details</h3>
""" + IDENTITY + """
    <label for="grad_year">Graduation Year <span style="color:#c0392b">*</span></label>
    <select id="grad_year" name="graduation_year" required>
      <option value="">Select...</option>
      <option value="2026">2026</option>
      <option value="2027">2027</option>
    </select>
    <div class="error" id="grad_year_error" role="alert">Graduation year is required</div>
""" + DROPDOWN_RADIO + CHECKBOX_TERMS + """
    <button type="submit" id="ef_submit" style="background:#0a7f24">Submit Application</button>
  </form>
  <div class="confirmation" id="confirmation">
    <h2>Thanks for your application!</h2>
    <p>Application EF-SBX-88231 is in review. We parsed your resume successfully.</p>
  </div>
""",
    """
  document.getElementById('ef_continue').addEventListener('click', function() {
    if (!document.getElementById('resume').files.length) { show('#resume_error'); return; }
    hide('#ef_upload_step');
    var f = document.getElementById('ef_form'); f.style.display = 'block';
    // "parsed" prefill like real Eightfold: leave fields empty for the engine to fill
  });
  document.getElementById('ef_submit').addEventListener('click', function(ev) {
    ev.preventDefault();
    if (!validateRequired(['first_name', 'last_name', 'email', 'grad_year'])) return;
    hide('#ef_form'); show('#confirmation');
  });
""")

# ---------------------------------------------------------------- Phenom (career-site wrapper -> underlying form injected on click)

PHENOM = page(
    "Phenom Sandbox - Career Site Wrapper",
    """
  <h1>Sandbox Corp Careers (powered by Phenom)</h1>
  <p>Data Analyst, Graduate Program - Sandbox Corp (Gurugram)</p>
  <div id="phenom_wrapper" class="ph-landing">
    <button type="button" id="phenom_apply" class="ph-apply-button" style="background:#0057d8">
      Apply Now
    </button>
  </div>
  <!-- templates the wrapper injects on click (kept out of the initial DOM so
       detection at wrapper stage reports phenom, not the underlying ATS) -->
  <div style="display:none" data-templates>
    <div id="identity_template">""" + IDENTITY + """</div>
    <div id="upload_template">""" + UPLOAD + """</div>
    <div id="dropdown_template">""" + DROPDOWN_RADIO + """</div>
    <div id="terms_template">""" + CHECKBOX_TERMS + """</div>
  </div>
  <div class="confirmation" id="confirmation">
    <h2>Application submitted</h2>
    <p>Confirmation PH-SBX-41002 emailed to you.</p>
  </div>
""",
    """
  // Phenom career pages hand off to the underlying ATS on Apply — the sandbox
  // injects the embedded (SmartRecruiters-style) form into the DOM on click,
  // exactly like a redirect reveals the underlying platform.
  var EMBEDDED = '<form id="phenom_form" class="smartrecruiters-embed sr-application-form" novalidate>' +
    '<h3>Application Form (underlying ATS)</h3>' +
    document.getElementById('identity_template').innerHTML +
    document.getElementById('upload_template').innerHTML +
    document.getElementById('dropdown_template').innerHTML +
    document.getElementById('terms_template').innerHTML +
    '<button type="submit" id="ph_submit" style="background:#0a7f24">Submit</button></form>';
  document.getElementById('phenom_apply').addEventListener('click', function() {
    hide('#phenom_wrapper');
    var container = document.createElement('div');
    container.innerHTML = EMBEDDED;
    document.body.insertBefore(container.firstChild, document.querySelector('.confirmation'));
    // remove the hidden template container so injected IDs stay unique
    var tpl = document.querySelector('div[data-templates]');
    if (tpl) tpl.remove();
    document.getElementById('ph_submit').addEventListener('click', function(ev) {
      ev.preventDefault();
      if (!validateRequired(['first_name', 'last_name', 'email'])) return;
      if (!document.getElementById('resume').files.length) { show('#resume_error'); return; }
      hide('#phenom_form'); show('#confirmation');
    });
  });
""")


FIXTURES.mkdir(parents=True, exist_ok=True)
for name, html in {
    "icims.html": ICIMS, "taleo.html": TALEO, "darwinbox.html": DARWINBOX,
    "zoho.html": ZOHO, "successfactors.html": SUCCESSFACTORS,
    "oracle_recruiting.html": ORACLE, "eightfold.html": EIGHTFOLD,
    "phenom.html": PHENOM,
}.items():
    (FIXTURES / name).write_text(html, encoding="utf-8")
    print(f"wrote {FIXTURES / name} ({len(html)} bytes)")
