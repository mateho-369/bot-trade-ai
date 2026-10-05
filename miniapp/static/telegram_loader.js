"use strict";
// The synthetic preview MUST NOT load Telegram or manufacture an identity.
(() => {
  if (document.documentElement.dataset.preview === "true") return;
  const script = document.createElement("script");
  script.src = "https://telegram.org/js/telegram-web-app.js";
  script.async = true;
  script.referrerPolicy = "no-referrer";
  script.onload = () => window.dispatchEvent(new Event("reflex-telegram-sdk-ready"));
  script.onerror = () => window.dispatchEvent(new Event("reflex-telegram-sdk-failed"));
  document.head.appendChild(script);
})();
