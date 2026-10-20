// Dashboard tab "Pronunciation": a teacher's own word list of terms the avatar should pronounce
// differently ("pH" -> "p H", "{number} m/s" -> "{number} Meter pro Sekunde"), per spoken
// language. It applies to the speech of all their projects in that language. Plain text only —
// no regular expressions — see backend/app/features/ai/tts/pronunciation.py for how it matches.

import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Callout } from "@/components/Callout";
import { Button } from "@/components/Button";
import { PageHeader } from "@/components/PageHeader";
import { errorMessage } from "@/api/client";
import { pronunciationApi, type PronunciationEntryInput } from "@/api/pronunciation";
import type { SpokenLanguage } from "@/types/project";
import { EntryFields, EMPTY_INPUT } from "./EntryFields";
import { EntryList } from "./EntryList";
import { ImportExportCard } from "./ImportExportCard";
import { PresetsCard } from "./PresetsCard";
import { PRONUNCIATION_QUERY_KEY } from "./queries";
import { TestBox } from "./TestBox";
import styles from "./Pronunciation.module.css";

/** The pronunciation word list tab. */
export function PronunciationPage() {
  const { t, i18n } = useTranslation();
  const [language, setLanguage] = useState<SpokenLanguage>(i18n.language.startsWith("en") ? "en" : "de");

  return (
    <div className={styles.page}>
      <PageHeader title={t("pronunciation.title")} subtitle={t("pronunciation.subtitle")} />

      <div className={styles.languageTabs} role="tablist" aria-label={t("pronunciation.language")}>
        {(["de", "en"] as const).map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={language === value}
            className={language === value ? styles.languageTabActive : styles.languageTab}
            onClick={() => setLanguage(value)}
          >
            {t(`configurator.step2.spokenLanguageOptions.${value}`)}
          </button>
        ))}
      </div>

      <Callout variant="info">{t("pronunciation.appliesTo")}</Callout>

      <details className={styles.card}>
        <summary className={styles.summary}>{t("pronunciation.howTo.title")}</summary>
        <ul className={styles.tips}>
          <li>{t("pronunciation.howTo.term")}</li>
          <li>{t("pronunciation.howTo.number")}</li>
          <li>{t("pronunciation.howTo.wholeWord")}</li>
          <li>{t("pronunciation.howTo.caseSensitive")}</li>
          <li>{t("pronunciation.howTo.spellOut")}</li>
          <li>{t("pronunciation.howTo.order")}</li>
        </ul>
      </details>

      <AddEntry language={language} />
      <TestBox language={language} />
      <EntryList language={language} />
      <PresetsCard language={language} />
      <ImportExportCard language={language} />
    </div>
  );
}

/** Form to add one term. */
function AddEntry({ language }: { language: SpokenLanguage }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [input, setInput] = useState<PronunciationEntryInput>(EMPTY_INPUT);

  const createMutation = useMutation({
    mutationFn: () => pronunciationApi.create(language, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: PRONUNCIATION_QUERY_KEY });
      setInput(EMPTY_INPUT);
    },
  });

  const canSubmit = input.term.trim() !== "" && (input.spellOut || input.spoken.trim() !== "");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (canSubmit) createMutation.mutate();
  }

  return (
    <form className={styles.card} onSubmit={handleSubmit}>
      <h3>{t("pronunciation.addTitle")}</h3>
      <EntryFields value={input} onChange={setInput} idPrefix="new-entry" />
      {createMutation.isError && (
        <Callout variant="danger">{errorMessage(createMutation.error, t("pronunciation.saveFailed"))}</Callout>
      )}
      <div>
        <Button type="submit" variant="accent" disabled={!canSubmit || createMutation.isPending}>
          {createMutation.isPending ? t("pronunciation.saving") : t("pronunciation.add")}
        </Button>
      </div>
    </form>
  );
}

