// Dashboard tab "Knowledge": a teacher's knowledge bases (RAG) — create one, upload documents,
// watch them get indexed, try a search, delete. Projects attach knowledge bases in the
// Configurator (step 3). Only reachable when the deployment runs the knowledge service.

import { Fragment, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { BookMarked, ChevronDown, ChevronRight, RotateCcw, Search, Trash2 } from "lucide-react";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { HeadingWithInfo, LabelWithInfo } from "@/components/InfoTip";
import { Input } from "@/components/Input";
import { PageHeader } from "@/components/PageHeader";
import { apiKeysApi } from "@/api/apiKeys";
import { errorMessage } from "@/api/client";
import {
  knowledgeApi,
  type KnowledgeBase,
  type KnowledgeDocument,
  type KnowledgePassage,
  type KnowledgeStatus,
} from "@/api/knowledge";
import { formatBytes, numberLocale } from "@/lib/format";
import { keyDisplayName, useKnowledgeStatus, useProviders } from "@/lib/providers";
import styles from "./Knowledge.module.css";
import { Bibliography, SourceForm, SourceSummary } from "./Sources";

const ACCEPT = ".pdf,.docx,.txt,.md,.markdown";
// How often the document list refreshes while something is still being indexed.
const POLL_MS = 3000;

/** The knowledge tab. */
export function KnowledgePage() {
  const { t } = useTranslation();
  const statusQuery = useKnowledgeStatus();
  const status = statusQuery.data;
  const kbQuery = useQuery({ queryKey: ["knowledge-bases"], queryFn: knowledgeApi.list, enabled: Boolean(status?.available) });
  const [openId, setOpenId] = useState<string | null>(null);

  if (statusQuery.isLoading) return <p className={styles.hint}>{t("common.loading")}</p>;
  if (!status?.available) {
    return (
      <div className={styles.page}>
        <Header />
        <Callout variant="info">{t("knowledge.disabled")}</Callout>
      </div>
    );
  }

  const knowledgeBases = kbQuery.data ?? [];

  return (
    <div className={styles.page}>
      <Header />
      {!status.reachable && <Callout variant="warning">{t("knowledge.unreachable")}</Callout>}
      <Usage status={status} knowledgeBaseCount={knowledgeBases.length} />
      <CreateKnowledgeBase status={status} onCreated={setOpenId} />

      <div className={styles.card}>
        <h3>{t("knowledge.listTitle")}</h3>
        {kbQuery.isLoading && <p className={styles.hint}>{t("common.loading")}</p>}
        {!kbQuery.isLoading && knowledgeBases.length === 0 && <p className={styles.hint}>{t("knowledge.empty")}</p>}
        <ul className={styles.kbList}>
          {knowledgeBases.map((kb) => (
            <KnowledgeBaseItem
              key={kb.id}
              kb={kb}
              status={status}
              open={openId === kb.id}
              onToggle={() => setOpenId(openId === kb.id ? null : kb.id)}
            />
          ))}
        </ul>
      </div>
    </div>
  );
}

function Header() {
  const { t } = useTranslation();
  return <PageHeader title={t("knowledge.title")} info={t("knowledge.subtitle")} />;
}

/** A labelled bar for how much of a limit is used — amber from 80 %, red when it's full. */
function QuotaMeter({ label, used, total, text }: { label: string; used: number; total: number; text: string }) {
  const percent = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  const level = percent >= 100 ? styles.meterFull : percent >= 80 ? styles.meterHigh : "";
  return (
    <div className={styles.meter}>
      <div className={styles.meterLabel}>
        <span>{label}</span>
        <span>{text}</span>
      </div>
      <div
        className={`${styles.meterBar} ${level}`}
        role="progressbar"
        aria-label={label}
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <span style={{ width: `${Math.max(percent, used > 0 ? 2 : 0)}%` }} />
      </div>
    </div>
  );
}

/** The teacher's limits at a glance: storage and number of knowledge bases. */
function Usage({ status, knowledgeBaseCount }: { status: KnowledgeStatus; knowledgeBaseCount: number }) {
  const { t } = useTranslation();
  if (!status.limits) return null;
  const quotaBytes = status.limits.userQuotaMb * 1024 * 1024;
  return (
    <div className={styles.card}>
      <h3>{t("knowledge.quotaTitle")}</h3>
      <div className={styles.meters}>
        <QuotaMeter
          label={t("knowledge.quotaStorage")}
          used={status.usageBytes}
          total={quotaBytes}
          text={t("knowledge.usage", { used: formatBytes(status.usageBytes), total: formatBytes(quotaBytes) })}
        />
        <QuotaMeter
          label={t("knowledge.quotaBases")}
          used={knowledgeBaseCount}
          total={status.limits.maxKbPerUser}
          text={t("knowledge.quotaCount", { used: knowledgeBaseCount, total: status.limits.maxKbPerUser })}
        />
      </div>
    </div>
  );
}

/** Form to create a knowledge base: a name, an optional description, and how it's embedded. */
function CreateKnowledgeBase({ status, onCreated }: { status: KnowledgeStatus; onCreated: (id: string) => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const specs = useProviders().data ?? [];
  const keysQuery = useQuery({ queryKey: ["api-keys"], queryFn: apiKeysApi.list });
  const embeddingKeys = (keysQuery.data ?? []).filter((key) => key.keyType === "embedding" && key.modelId);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  // "" = the knowledge service's own local model.
  const [embeddingKeyId, setEmbeddingKeyId] = useState("");

  const createMutation = useMutation({
    mutationFn: () => knowledgeApi.create(name.trim(), description.trim() || null, embeddingKeyId || null),
    onSuccess: (kb) => {
      queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
      setName("");
      setDescription("");
      onCreated(kb.id);
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (name.trim()) createMutation.mutate();
  }

  return (
    <form className={styles.card} onSubmit={handleSubmit}>
      <h3>{t("knowledge.createTitle")}</h3>
      <Input
        label={t("knowledge.name")}
        placeholder={t("knowledge.namePlaceholder")}
        value={name}
        maxLength={100}
        onChange={(e) => setName(e.target.value)}
      />
      <Input
        label={t("knowledge.descriptionOptional")}
        value={description}
        maxLength={500}
        onChange={(e) => setDescription(e.target.value)}
      />
      <div className={styles.field}>
        <LabelWithInfo
          label={t("knowledge.embedding")}
          htmlFor="kb-embedding"
          className={styles.label}
          info={t("knowledge.embeddingHint")}
        />
        <select
          id="kb-embedding"
          className={styles.select}
          value={embeddingKeyId}
          onChange={(e) => setEmbeddingKeyId(e.target.value)}
        >
          <option value="">{t("knowledge.embeddingLocal", { model: status.localModel ?? "—" })}</option>
          {embeddingKeys.map((key) => (
            <option key={key.id} value={key.id}>
              {keyDisplayName(key, specs)} · {key.modelId}
            </option>
          ))}
        </select>
        {embeddingKeyId && <Callout variant="warning">{t("knowledge.embeddingApiNotice")}</Callout>}
      </div>
      {createMutation.isError && (
        <Callout variant="danger">{errorMessage(createMutation.error, t("knowledge.createFailed"))}</Callout>
      )}
      <div>
        <Button type="submit" variant="accent" disabled={!name.trim() || createMutation.isPending}>
          {t("knowledge.create")}
        </Button>
      </div>
    </form>
  );
}

function KnowledgeBaseItem({
  kb,
  status,
  open,
  onToggle,
}: {
  kb: KnowledgeBase;
  status: KnowledgeStatus;
  open: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const removeMutation = useMutation({
    mutationFn: () => knowledgeApi.remove(kb.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
      queryClient.invalidateQueries({ queryKey: ["rag-status"] });
      // Projects that used it lost the link.
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  function handleRemove() {
    const message = kb.usedByProjects
      ? t("knowledge.removeConfirmUsed", { name: kb.name, count: kb.usedByProjects })
      : t("knowledge.removeConfirm", { name: kb.name });
    if (window.confirm(message)) removeMutation.mutate();
  }

  return (
    <li className={styles.kb}>
      <div className={styles.kbTop}>
        <button type="button" className={styles.kbToggle} onClick={onToggle} aria-expanded={open}>
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          <span>
            <strong>{kb.name}</strong>
            <span className={styles.hint}>
              {t("knowledge.kbMeta", {
                count: kb.documentCount,
                size: formatBytes(kb.sizeBytes),
                projects: kb.usedByProjects,
              })}
            </span>
            {kb.description && <span className={styles.hint}>{kb.description}</span>}
          </span>
        </button>
        {status.limits && (
          <div className={styles.kbMeter}>
            <QuotaMeter
              label={t("knowledge.quotaDocuments")}
              used={kb.documentCount}
              total={status.limits.maxDocumentsPerKb}
              text={t("knowledge.quotaCount", { used: kb.documentCount, total: status.limits.maxDocumentsPerKb })}
            />
          </div>
        )}
        <div className={styles.kbActions}>
          <Badge variant={kb.embeddingMode === "local" ? "default" : "accent"} title={kb.embeddingModel}>
            {kb.embeddingMode === "local" ? t("knowledge.embeddingBadgeLocal") : t("knowledge.embeddingBadgeApi")}
          </Badge>
          <Button
            size="sm"
            onClick={handleRemove}
            disabled={removeMutation.isPending}
            aria-label={t("knowledge.remove")}
            title={t("knowledge.remove")}
          >
            <Trash2 size={14} />
          </Button>
        </div>
      </div>
      {!kb.embeddingAvailable && <Callout variant="warning">{t("knowledge.embeddingUnavailable")}</Callout>}
      {removeMutation.isError && (
        <Callout variant="danger">{errorMessage(removeMutation.error, t("knowledge.removeFailed"))}</Callout>
      )}
      {open && <KnowledgeBaseDetail kb={kb} status={status} />}
    </li>
  );
}

function KnowledgeBaseDetail({ kb, status }: { kb: KnowledgeBase; status: KnowledgeStatus }) {
  const documentsQuery = useQuery({
    queryKey: ["knowledge-documents", kb.id],
    queryFn: () => knowledgeApi.documents(kb.id),
    // Indexing happens in the background — poll only while something is still in progress.
    refetchInterval: (query) =>
      (query.state.data ?? []).some((d) => d.status === "queued" || d.status === "processing") ? POLL_MS : false,
  });
  const documents = documentsQuery.data ?? [];

  return (
    <div className={styles.detail}>
      <UploadDocuments kb={kb} status={status} />
      <Bibliography kb={kb} />
      <DocumentTable kb={kb} documents={documents} loading={documentsQuery.isLoading} doclingAvailable={status.doclingAvailable} />
      {documents.some((d) => d.status === "ready") && <TestSearch kb={kb} />}
    </div>
  );
}

function UploadDocuments({ kb, status }: { kb: KnowledgeBase; status: KnowledgeStatus }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [files, setFiles] = useState<File[]>([]);
  const [parser, setParser] = useState<"light" | "docling">("light");
  const [consent, setConsent] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  // Remounts the file input after an upload, clearing it.
  const [inputKey, setInputKey] = useState(0);
  const maxMb = status.limits?.maxUploadMb ?? 20;

  const uploadMutation = useMutation({
    // One request per file, one after another: each is checked and quota-counted on its own, and a
    // rejected file doesn't stop the rest.
    mutationFn: async () => {
      const failed: string[] = [];
      for (const file of files) {
        if (file.size > maxMb * 1024 * 1024) {
          failed.push(`${file.name}: ${t("errors.KNOWLEDGE_FILE_TOO_LARGE")}`);
          continue;
        }
        try {
          await knowledgeApi.upload(kb.id, file, parser, consent);
        } catch (error) {
          failed.push(`${file.name}: ${errorMessage(error, t("knowledge.uploadFailed"))}`);
        }
      }
      return failed;
    },
    onSuccess: (failed) => {
      setErrors(failed);
      setFiles([]);
      setInputKey((key) => key + 1);
      queryClient.invalidateQueries({ queryKey: ["knowledge-documents", kb.id] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
      queryClient.invalidateQueries({ queryKey: ["rag-status"] });
    },
  });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (files.length && consent) uploadMutation.mutate();
  }

  return (
    <form className={styles.upload} onSubmit={handleSubmit}>
      <h4>{t("knowledge.uploadTitle")}</h4>
      <p className={styles.hint}>
        {t("knowledge.uploadHint", { mb: maxMb, pages: status.limits?.maxPages ?? 500 })}
      </p>
      <input
        key={inputKey}
        type="file"
        multiple
        accept={ACCEPT}
        aria-label={t("knowledge.chooseFiles")}
        onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
      />
      {status.doclingAvailable && (
        <div className={styles.field}>
          <label className={styles.label} htmlFor={`parser-${kb.id}`}>
            {t("knowledge.parser")}
          </label>
          <select
            id={`parser-${kb.id}`}
            className={styles.select}
            value={parser}
            onChange={(e) => setParser(e.target.value as "light" | "docling")}
          >
            <option value="light">{t("knowledge.parserLight")}</option>
            <option value="docling">{t("knowledge.parserDocling")}</option>
          </select>
        </div>
      )}
      <label className={styles.consent}>
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        <span>{t("knowledge.consent")}</span>
      </label>
      {errors.length > 0 && (
        <Callout variant="danger">
          <ul className={styles.errorList}>
            {errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </Callout>
      )}
      <div>
        <Button type="submit" variant="accent" disabled={!files.length || !consent || uploadMutation.isPending}>
          {uploadMutation.isPending
            ? t("knowledge.uploading")
            : files.length
              ? t("knowledge.upload", { count: files.length })
              : t("knowledge.uploadNone")}
        </Button>
      </div>
    </form>
  );
}

function DocumentTable({
  kb,
  documents,
  loading,
  doclingAvailable,
}: {
  kb: KnowledgeBase;
  documents: KnowledgeDocument[];
  loading: boolean;
  doclingAvailable: boolean;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const retryMutation = useMutation({
    mutationFn: ({ id, parser }: { id: string; parser: "light" | "docling" }) => knowledgeApi.retry(id, parser),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["knowledge-documents", kb.id] }),
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => knowledgeApi.removeDocument(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["knowledge-documents", kb.id] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-bases"] });
      queryClient.invalidateQueries({ queryKey: ["rag-status"] });
    },
  });

  const [editingId, setEditingId] = useState<string | null>(null);

  if (loading) return <p className={styles.hint}>{t("common.loading")}</p>;
  if (documents.length === 0) return <p className={styles.hint}>{t("knowledge.noDocuments")}</p>;

  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead>
          <tr>
            <th>{t("knowledge.table.file")}</th>
            <th>{t("knowledge.table.status")}</th>
            <th>{t("knowledge.table.details")}</th>
            <th aria-label={t("knowledge.table.actions")} />
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => (
            <Fragment key={doc.id}>
            <tr>
              <td>
                <span className={styles.filename}>{doc.filename}</span>
                <SourceSummary document={doc} />
                <span className={styles.hint}>
                  {formatBytes(doc.sizeBytes)} · {new Date(doc.createdAt).toLocaleDateString(numberLocale())}
                </span>
              </td>
              <td>
                <Badge variant={doc.status === "failed" ? "danger" : doc.status === "ready" ? "accent" : "default"}>
                  {t(`knowledge.status.${doc.status}`)}
                </Badge>
              </td>
              <td className={styles.details}>
                {doc.status === "ready" && (
                  <>
                    {doc.pageCount ? t("knowledge.pages", { count: doc.pageCount }) + " · " : ""}
                    {t("knowledge.chunks", { count: doc.chunkCount ?? 0 })}
                    {doc.parser === "docling" && ` · Docling`}
                    {doc.truncated && <span className={styles.warning}> · {t("knowledge.truncated")}</span>}
                  </>
                )}
                {doc.status === "failed" && (
                  <>
                    <span className={styles.warning}>
                      {doc.errorCode ? t(`errors.${doc.errorCode}`, t("knowledge.failedGeneric")) : t("knowledge.failedGeneric")}
                    </span>
                    {doc.retryable ? (
                      <span className={styles.retryRow}>
                        <Button
                          size="sm"
                          onClick={() => retryMutation.mutate({ id: doc.id, parser: doc.parser })}
                          disabled={retryMutation.isPending}
                        >
                          <RotateCcw size={14} /> {t("knowledge.retry")}
                        </Button>
                        {/* A scan without a text layer needs OCR — that's what Docling adds. */}
                        {doclingAvailable && doc.parser !== "docling" && (doc.fileType === "pdf" || doc.fileType === "docx") && (
                          <Button
                            size="sm"
                            onClick={() => retryMutation.mutate({ id: doc.id, parser: "docling" })}
                            disabled={retryMutation.isPending}
                          >
                            <RotateCcw size={14} /> {t("knowledge.retryDocling")}
                          </Button>
                        )}
                      </span>
                    ) : (
                      <span className={styles.hint}>{t("knowledge.retryReupload")}</span>
                    )}
                  </>
                )}
                {(doc.status === "queued" || doc.status === "processing") && t("knowledge.inProgress")}
              </td>
              <td className={styles.rowActions}>
                <Button
                  size="sm"
                  onClick={() => setEditingId(editingId === doc.id ? null : doc.id)}
                  aria-expanded={editingId === doc.id}
                  aria-label={t("sources.edit", { name: doc.filename })}
                  title={t("sources.editShort")}
                >
                  <BookMarked size={14} />
                </Button>
                <Button
                  size="sm"
                  onClick={() => {
                    if (window.confirm(t("knowledge.removeDocumentConfirm", { name: doc.filename }))) {
                      removeMutation.mutate(doc.id);
                    }
                  }}
                  disabled={removeMutation.isPending}
                  aria-label={t("knowledge.removeDocument")}
                  title={t("knowledge.removeDocument")}
                >
                  <Trash2 size={14} />
                </Button>
              </td>
            </tr>
            {editingId === doc.id && (
              <tr>
                <td colSpan={4}>
                  <SourceForm document={doc} kb={kb} onClose={() => setEditingId(null)} />
                </td>
              </tr>
            )}
            </Fragment>
          ))}
        </tbody>
      </table>
      {removeMutation.isError && (
        <Callout variant="danger">{errorMessage(removeMutation.error, t("knowledge.removeFailed"))}</Callout>
      )}
      {retryMutation.isError && (
        <Callout variant="danger">{errorMessage(retryMutation.error, t("knowledge.retryFailed"))}</Callout>
      )}
    </div>
  );
}

/** "What would a student's question retrieve?" — the passages, with file and page. */
function TestSearch({ kb }: { kb: KnowledgeBase }) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const searchMutation = useMutation({ mutationFn: () => knowledgeApi.search(kb.id, query.trim()) });

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (query.trim()) searchMutation.mutate();
  }

  return (
    <form className={styles.search} onSubmit={handleSubmit}>
      <HeadingWithInfo as="h4" title={t("knowledge.searchTitle")} info={t("knowledge.searchHint")} />
      <div className={styles.searchRow}>
        <input
          className={styles.searchInput}
          value={query}
          maxLength={2000}
          placeholder={t("knowledge.searchPlaceholder")}
          aria-label={t("knowledge.searchTitle")}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button type="submit" size="sm" disabled={!query.trim() || searchMutation.isPending}>
          <Search size={14} /> {t("knowledge.searchButton")}
        </Button>
      </div>
      {searchMutation.isError && (
        <Callout variant="danger">{errorMessage(searchMutation.error, t("knowledge.searchFailed"))}</Callout>
      )}
      {searchMutation.isSuccess && <SearchResults passages={searchMutation.data} />}
    </form>
  );
}

function SearchResults({ passages }: { passages: KnowledgePassage[] }) {
  const { t } = useTranslation();
  if (passages.length === 0) return <p className={styles.hint}>{t("knowledge.searchEmpty")}</p>;
  return (
    <ol className={styles.passages}>
      {passages.map((passage) => (
        <li key={passage.chunkId}>
          <span className={styles.passageSource}>
            {passage.filename ?? t("knowledge.deletedDocument")}
            {passage.heading ? ` · ${passage.heading}` : ""}
            {passage.page ? ` · ${t("knowledge.page", { page: passage.page })}` : ""}
          </span>
          <p className={styles.passageText}>{passage.text}</p>
        </li>
      ))}
    </ol>
  );
}
