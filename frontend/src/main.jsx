import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const endpoint = "/api/v1/internal-linking/analyze";

function App() {
  const [targetUrl, setTargetUrl] = useState("");
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setResult("");
    setIsLoading(true);

    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          target_url: targetUrl.trim()
        })
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        const detail = payload?.detail ? JSON.stringify(payload.detail) : response.statusText;
        throw new Error(detail);
      }

      const payload = await response.json();
      setResult(payload.message || "Анализ завершен.");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Не удалось выполнить анализ");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="app-shell">
      <section className="panel" aria-label="Анализ внутренней перелинковки">
        <a className="brand" href="http://80.93.62.177:8000/" aria-label="ДИО-Консалт">
          <img
            className="brand-logo"
            src="http://80.93.62.177:8000/media/images/Logo_bez_fona_bez_teksta.width-80.height-80.png"
            alt=""
          />
          <span className="brand-name">ДИО-Консалт</span>
        </a>

        <form className="analysis-form" onSubmit={handleSubmit}>
          <label htmlFor="target-url">Введите значение</label>
          <textarea
            id="target-url"
            value={targetUrl}
            onChange={(event) => setTargetUrl(event.target.value)}
            placeholder="https://example.com/catalog/target-page"
            rows={5}
            required
          />
          <button type="submit" disabled={isLoading || !targetUrl.trim()}>
            {isLoading ? "Идет анализ..." : "Проверить"}
          </button>
        </form>

        <section className="result" aria-live="polite">
          <h1>Результат</h1>
          {error ? <p className="error-text">{error}</p> : <p>{result || "Здесь появится ответ программы."}</p>}
        </section>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
