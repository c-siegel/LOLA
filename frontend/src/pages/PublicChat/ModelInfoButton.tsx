import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { HelpCircle } from "lucide-react";
import styles from "./PublicChat.module.css";

/** The "?" in the chat header: a speech bubble naming the AI model behind the tutor and saying
 * whether the conversation is saved. */
export function ModelInfoButton({ llmModel, privacyNotice }: { llmModel: string | null; privacyNotice: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const bubbleId = useId();

  // Opens on tap/click, not hover or focus: iOS Safari doesn't focus a button on tap, so a
  // :focus-within bubble never opened on iPhones. Escape and a tap anywhere else close it again.
  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    function handlePointerDown(event: PointerEvent) {
      if (!wrapperRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("pointerdown", handlePointerDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("pointerdown", handlePointerDown);
    };
  }, [open]);

  return (
    <span className={`${styles.infoTip} ${open ? styles.infoTipOpen : ""}`} ref={wrapperRef}>
      <button
        type="button"
        className={styles.infoButton}
        aria-label={t("publicChat.chatInfo")}
        aria-expanded={open}
        aria-controls={bubbleId}
        onClick={() => setOpen((value) => !value)}
      >
        <HelpCircle size={24} />
      </button>
      <span className={styles.infoTipBubble} id={bubbleId}>
        <span className={styles.infoTipLine}>
          {llmModel ? t("publicChat.modelTooltip", { model: llmModel }) : t("publicChat.modelTooltipUnknown")}
        </span>
        <span className={styles.infoTipLine}>{privacyNotice}</span>
      </span>
    </span>
  );
}
