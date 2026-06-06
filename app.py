import os
import json
import base64
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from google import genai
from google.genai import types

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "truthlens_secret_key_session_12345")


def get_gemini_client(api_key=None):
    key = api_key or session.get("api_key") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise ValueError("Gemini API key is not configured. Please enter your API key in the settings below.")
    return genai.Client(api_key=key)


def generate_content_with_fallback(client, contents, config):
    models_to_try = ["gemini-2.5-flash-lite"]
    last_error = None
    for model in models_to_try:
        try:
            print(f"Trying Gemini model: {model}")
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
            return response
        except Exception as e:
            print(f"Model {model} failed with: {e}")
            last_error = e
            continue
    raise last_error


def analyze_claim(claim_text, file_data=None, api_key=None):
    client = get_gemini_client(api_key)
    
    system_instruction = (
        "You are a professional fact-checker. Analyze the user claim. Search the web and return ONLY the raw JSON object matching the requested structure. Do not output any markdown code blocks, ticks, or text outside the JSON."
    )
    
    prompt = f"""Analyse this claim:
"{claim_text}"

Search the web and return JSON:
{{
  "verdict": "TRUE" | "LIKELY TRUE" | "MISLEADING" | "LIKELY FALSE" | "FALSE",
  "credibility_score": 0-100,
  "summary": "2-line plain English verdict",
  "claims": [{{ "claim": string, "verdict": string, "explanation": string }}],
  "sources": [{{ "title": string, "url": string, "credibility": "High" | "Mid" | "Low" }}],
  "context": "what actually happened / full picture"
}}"""

    contents = []
    if file_data:
        contents.append(
            types.Part.from_bytes(
                data=file_data["data"],
                mime_type=file_data["mime_type"]
            )
        )
    contents.append(prompt)
    
    google_search_tool = types.Tool(
        google_search=types.GoogleSearch()
    )
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[google_search_tool]
    )
    
    response = generate_content_with_fallback(client, contents, config)
    
    response_text = response.text.strip()
    
    if response_text.startswith("```"):
        lines = response_text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        response_text = "\n".join(lines).strip()
        
    return json.loads(response_text)


def calculate_session_statistics(history):
    total_checks = len(history)
    if total_checks == 0:
        return {
            "total_checks": 0,
            "true_checks": 0,
            "false_checks": 0,
            "accuracy_ratio_percent": 0,
            "verdict_distribution": {
                "true_percent": 0,
                "false_percent": 0,
                "misleading_percent": 0
            }
        }
        
    true_checks = sum(1 for item in history if item.get("verdict") in ["TRUE", "LIKELY TRUE"])
    false_checks = sum(1 for item in history if item.get("verdict") in ["FALSE", "LIKELY FALSE"])
    misleading_checks = sum(1 for item in history if item.get("verdict") == "MISLEADING")
    
    accuracy_ratio_percent = int(round((true_checks / total_checks) * 100))
    
    true_percent = int(round((true_checks / total_checks) * 100))
    false_percent = int(round((false_checks / total_checks) * 100))
    misleading_percent = int(round((misleading_checks / total_checks) * 100))
    
    return {
        "total_checks": total_checks,
        "true_checks": true_checks,
        "false_checks": false_checks,
        "accuracy_ratio_percent": accuracy_ratio_percent,
        "verdict_distribution": {
            "true_percent": true_percent,
            "false_percent": false_percent,
            "misleading_percent": misleading_percent
        }
    }


def get_session_statistics(history, api_key=None):
    if not history:
        return {
            "total_checks": 0,
            "true_checks": 0,
            "false_checks": 0,
            "accuracy_ratio_percent": 0,
            "verdict_distribution": {
                "true_percent": 0,
                "false_percent": 0,
                "misleading_percent": 0
            }
        }
    
    key = api_key or session.get("api_key") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        try:
            client = genai.Client(api_key=key)
            prompt = f"""
Analyze the user's session and historical data:
{json.dumps(history)}

Return the following stats in JSON format:
{{
  "total_checks": integer,
  "true_checks": integer,
  "false_checks": integer,
  "accuracy_ratio_percent": integer,
  "verdict_distribution": {{
    "true_percent": integer,
    "false_percent": integer,
    "misleading_percent": integer
  }}
}}"""
            
            config = types.GenerateContentConfig(
                response_mime_type="application/json"
            )
            response = generate_content_with_fallback(client, prompt, config)
            return json.loads(response.text)
        except Exception as e:
            print("Gemini statistics API call failed, falling back to local calculation:", e)
            
    return calculate_session_statistics(history)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/save_api_key", methods=["POST"])
def save_api_key():
    api_key = request.form.get("api_key")
    if not api_key and request.is_json:
        api_key = request.json.get("api_key")
    if api_key:
        session["api_key"] = api_key.strip()
        return jsonify({"status": "success", "message": "API Key saved successfully"})
    return jsonify({"status": "error", "message": "No API Key provided"}), 400


@app.route("/render_statistics", methods=["POST"])
def stats():
    return render_template("check.html")


@app.route("/check")
def check():
    return render_template("check.html")


@app.route("/render_analysis", methods=["POST"])
def render_analysis():
    claim_text = request.form.get("claim", "").strip()
    api_key = request.form.get("api_key", "").strip()
    
    if api_key:
        session["api_key"] = api_key
        
    file = request.files.get("file")
    file_data = None
    if file and file.filename != "":
        mime_type = file.content_type
        data = file.read()
        
        if mime_type.startswith("text/") or file.filename.endswith((".txt", ".md", ".json", ".csv")):
            text_content = data.decode("utf-8", errors="ignore")
            claim_text = f"{claim_text}\n\n[File Content: {file.filename}]\n{text_content}".strip()
        else:
            file_data = {
                "mime_type": mime_type,
                "data": data,
                "filename": file.filename
            }
            
    if not claim_text and not file_data:
        return render_template("check.html", error="Please enter a claim or upload a file.")
        
    try:
        result = analyze_claim(claim_text, file_data)
        
        session["last_analysis"] = result
        session["last_claim"] = claim_text
        
        history = session.get("history", [])
        history.append({
            "verdict": result.get("verdict", "FALSE")
        })
        session["history"] = history
        
        return render_template("analysis.html", **result, claim=claim_text)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return render_template("check.html", error=f"Analysis failed: {str(e)}")


@app.route("/analysis", methods=["GET"])
def analysis():
    return render_template("analysis.html", claim=None)


@app.route("/view_historical_analysis", methods=["POST"])
def view_historical_analysis():
    data = request.get_json()
    if data:
        session["last_analysis"] = {
            "verdict": data.get("verdict"),
            "credibility_score": data.get("credibility_score"),
            "summary": data.get("summary"),
            "claims": data.get("claims"),
            "sources": data.get("sources"),
            "context": data.get("context")
        }
        session["last_claim"] = data.get("claim")
        return jsonify({"status": "success", "redirect": url_for("analysis")})
    return jsonify({"status": "error", "message": "No data provided"}), 400


@app.route("/test_models")
def test_models():
    key = session.get("api_key") or os.environ.get("GEMINI_API_KEY")
    if not key:
        return "No API key found in session."
    try:
        client = genai.Client(api_key=key)
        models = [model.name for model in client.models.list()]
        return jsonify(models)
    except Exception as e:
        return str(e)


@app.route("/statistics")
def statistics():
    history = session.get("history", [])
    stats_data = get_session_statistics(history)
    return render_template("statistics.html", **stats_data)


if __name__ == "__main__":
    app.run(debug=True)
