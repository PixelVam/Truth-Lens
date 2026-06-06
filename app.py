from flask import Flask, render_template, request

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/render_statistics", methods=["POST"])
def stats():
    return render_template("check.html")


@app.route("/render_analysis", methods=["POST", "GET"])
def render_analysis():
    return render_template("analysis.html")


if __name__ == "__main__":
    app.run(debug=True)
