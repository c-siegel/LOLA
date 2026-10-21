// Initializes i18next once (imported for its side effect in main.tsx, before the app renders).
// Language choice lives in localStorage only — no account-level persistence.
import i18next from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import de from "./locales/de.json";
import en from "./locales/en.json";

export const LANGUAGE_STORAGE_KEY = "lola_lang";

i18next
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      de: { translation: de },
      en: { translation: en },
    },
    fallbackLng: "de",
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: LANGUAGE_STORAGE_KEY,
      caches: ["localStorage"],
    },
    interpolation: { escapeValue: false },
  });

// Keeps <html lang> (see index.html) in sync for screen readers and browser spell-check.
document.documentElement.lang = i18next.language;
i18next.on("languageChanged", (lng) => {
  document.documentElement.lang = lng;
});

export default i18next;
