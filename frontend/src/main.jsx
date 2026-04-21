const endpoint = "/analyze";

const form = document.getElementById("analysis-form");
const input = document.getElementById("target-url");
const button = document.getElementById("submit-button");
const resultText = document.getElementById("result-text");

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const targetUrl = input.value.trim();
  if (!targetUrl) {
    return;
  }

  setLoading(true);
  setResult("Идет анализ. Это может занять пару минут.");

  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        target_url: targetUrl,
      }),
    });

    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      const message = payload?.detail?.message || payload?.detail?.error || response.statusText;
      throw new Error(message);
    }

    const payload = await response.json();
    setResult(formatResult(payload));
  } catch (requestError) {
    setResult(
      requestError instanceof Error ? requestError.message : "Не удалось выполнить анализ",
      true
    );
  } finally {
    setLoading(false);
  }
});

function setLoading(isLoading) {
  input.disabled = isLoading;
  button.disabled = isLoading;
  button.textContent = isLoading ? "Идет анализ..." : "Проверить";
}

function setResult(message, isError = false) {
  resultText.className = isError ? "error-text" : "";
  resultText.textContent = message;
}

function formatResult(payload) {
  if (!payload || typeof payload !== "object") {
    return "Анализ завершен.";
  }

  const message = typeof payload.message === "string" ? payload.message.trim() : "";
  if (message) {
    return cleanupUserMessage(message);
  }

  return "Анализ завершен, но текстовый результат не был сформирован.";
}

function cleanupUserMessage(message) {
  return message
    .replace(/\s+/g, " ")
    .replace(/\s+(\d+\.)/g, "\n$1")
    .replace(/Кандидаты:/g, "\nКандидаты:")
    .replace(/Рекомендованные кандидаты:/g, "\nРекомендованные кандидаты:")
    .trim();
}
