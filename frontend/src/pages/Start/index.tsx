// The student start page (/start): one fixed address for shared devices such as class iPads, saved
// to the home screen as a web app. A student types the chat's key (the code after /c/ in its link)
// and lands in that chat — its password, if any, is asked there as usual. Meanwhile the page
// downloads the on-device speech models, so the chat itself starts without waiting for them.
//
// Nothing about a previous student survives a visit here: every chat's unlock and entered name
// are forgotten, and no "last chat" is remembered.

import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Input } from "@/components/Input";
import { Wordmark } from "@/components/Wordmark";
import { ApiError, errorMessage } from "@/api/client";
import { publicChatApi } from "@/api/publicChat";
import { settingsApi } from "@/api/settings";
import { PublicChatLayout } from "@/layouts/PublicChatLayout";
import { sitePath } from "@/lib/basePath";
import { clearUnlockTokens } from "@/lib/chatUnlockStorage";
import { modelPackages, prefetchModels, type ModelId, type PrefetchState } from "@/lib/modelPrefetch";
import { clearVisitorNames } from "@/lib/visitorNameStorage";
import { ModelStatus } from "./ModelStatus";
import styles from "./Start.module.css";

/** Router state a chat opened from here gets, so it can offer the way back (see PublicChat). */
export interface FromStartState {
  fromStart: true;
}

const INITIAL_STATES: Record<ModelId, PrefetchState> = {
  vad: { status: "waiting" },
  stt: { status: "waiting" },
  tts: { status: "waiting" },
};

/** Lets "Add to Home Screen" install this page — and only this one — as a standalone web app.
 * Added here rather than in index.html, so a teacher bookmarking the dashboard doesn't get the
 * student start page instead. */
function useWebAppManifest() {
  useEffect(() => {
    const elements = [
      Object.assign(document.createElement("link"), { rel: "manifest", href: sitePath("/start.webmanifest") }),
      Object.assign(document.createElement("meta"), { name: "apple-mobile-web-app-capable", content: "yes" }),
      Object.assign(document.createElement("meta"), { name: "mobile-web-app-capable", content: "yes" }),
      Object.assign(document.createElement("meta"), { name: "apple-mobile-web-app-title", content: "LOLA" }),
    ];
    elements.forEach((element) => document.head.appendChild(element));
    return () => elements.forEach((element) => element.remove());
  }, []);
}

function normalizeKey(value: string): string {
  // Keys are five capital letters (backend projects/publish.py); students type them however.
  return value.replace(/\s+/g, "").toUpperCase();
}

export function StartPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [key, setKey] = useState("");
  const [modelStates, setModelStates] = useState(INITIAL_STATES);
  const [prefetchRun, setPrefetchRun] = useState(0);
  useWebAppManifest();

  // The previous student's unlocks and name — including the cached "unlocked" chat responses.
  useEffect(() => {
    clearUnlockTokens();
    clearVisitorNames();
    queryClient.removeQueries({ queryKey: ["public-chat"] });
  }, [queryClient]);

  const settingsQuery = useQuery({ queryKey: ["settings", "public"], queryFn: settingsApi.getPublic });
  const sttUrl = settingsQuery.data?.browserSttModelUrl ?? null;
  const ttsUrl = settingsQuery.data?.browserTtsModelUrl ?? null;
  const settingsLoaded = settingsQuery.isSuccess;

  // Stops when the page is left: the chat's own workers take over from whatever is stored by then.
  useEffect(() => {
    if (!settingsLoaded) return;
    const abort = new AbortController();
    setModelStates(INITIAL_STATES);
    void prefetchModels(
      modelPackages({ stt: sttUrl, tts: ttsUrl }),
      (id, state) => setModelStates((previous) => ({ ...previous, [id]: state })),
      abort.signal,
    );
    return () => abort.abort();
  }, [settingsLoaded, sttUrl, ttsUrl, prefetchRun]);

  const openMutation = useMutation({
    // Same query the chat page reads, so it opens without a second request.
    mutationFn: (slug: string) =>
      queryClient.fetchQuery({ queryKey: ["public-chat", slug], queryFn: () => publicChatApi.loadTutor(slug), retry: false }),
    onSuccess: (_tutor, slug) => {
      const state: FromStartState = { fromStart: true };
      navigate(`/c/${slug}`, { state });
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const slug = normalizeKey(key);
    if (!slug || openMutation.isPending) return;
    openMutation.mutate(slug);
  }

  const openError = openMutation.error;
  const openErrorText =
    openError instanceof ApiError && openError.status === 404
      ? t("start.unknownKey")
      : errorMessage(openError, t("start.genericError"));

  return (
    <PublicChatLayout>
      <main className={styles.page}>
        <div className={styles.content}>
          <Wordmark />
          <form className={styles.form} onSubmit={handleSubmit}>
            <h1 className={styles.title}>{t("start.title")}</h1>
            <p className={styles.description}>{t("start.description")}</p>
            <Input
              label={t("start.keyLabel")}
              placeholder={t("start.keyPlaceholder")}
              className={styles.keyInput}
              value={key}
              onChange={(event) => setKey(event.target.value)}
              autoCapitalize="characters"
              autoComplete="off"
              autoCorrect="off"
              spellCheck={false}
              maxLength={20}
              required
            />
            {openMutation.isError && <Callout variant="danger">{openErrorText}</Callout>}
            <Button type="submit" variant="accent" fullWidth disabled={openMutation.isPending}>
              {t("start.submit")}
            </Button>
          </form>
          {settingsLoaded && <ModelStatus states={modelStates} onRetry={() => setPrefetchRun((run) => run + 1)} />}
        </div>
      </main>
    </PublicChatLayout>
  );
}
