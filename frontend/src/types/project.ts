export type ProjectStatus = "draft" | "published";

export interface Project {
  id: string;
  title: string;
  description: string | null;
  status: ProjectStatus;
  // Reference to the key set up in the API dashboard — the actual model choice.
  llmApiKeyId: string | null;
  // litellm model string the backend derives from it (read-only, used e.g. for project cards and
  // the analytics model filter).
  llmModel: string | null;
  preprompt: string;
  // The avatar's first message, visible to students AND given to the model as context (see backend
  // features/ai/llm/__init__.py::complete). Empty = generic greeting.
  startPrompt: string;
  // URL of the once-generated audio for startPrompt, or null if it hasn't been generated (yet)
  // — see the "Generate audio" button in Step3Behavior and pages/PublicChat/index.tsx's autoplay.
  startAudioUrl: string | null;
  // The avatar: one of the user's own library models (avatarModelId), or a bundled default avatar
  // by name (builtinAvatar, e.g. "julia" for public/avatars/julia.glb). Neither = default avatar.
  avatarModelId: string | null;
  builtinAvatar: string | null;
  avatarBackgroundId: string | null;
  // Ready-to-load URLs derived by the backend from the references above (read-only).
  avatarModelUrl: string | null;
  avatarBackgroundUrl: string | null;
  gradeLevel: string | null;
  // Sampling parameters, passed 1:1 to the provider (see backend features/ai/llm
  // ::_sampling_params). temperature 0.0-2.0, topP 0.0-1.0 — the backend checks the same limits.
  temperature: number;
  topP: number;
  published: boolean;
  shareSlug: string | null;
  saveConversations: boolean;
  surveyBeforeUrl: string | null;
  surveyBeforeEnabled: boolean;
  surveyAfterUrl: string | null;
  surveyAfterEnabled: boolean;
  ttsEnabled: boolean;
  // Reference to a key of type TTS (like llmApiKeyId).
  ttsApiKeyId: string | null;
  ttsVoice: string | null;
  // A clip from the owner's voice library (Dashboard → Voices) that local TTS clones the voice
  // from — only used while ttsApiKeyId is null. null speaks with the default local voice.
  ttsVoiceClipId: string | null;
  spokenLanguage: SpokenLanguage;
  // Reference to a key of type STT (mirrors ttsApiKeyId) — null keeps transcribing locally
  // (see backend features/ai/stt).
  sttApiKeyId: string | null;
  // Which local engine transcribes on the server when sttApiKeyId is null — null follows the
  // deployment's STT_ENGINE (see useServerSttStatus in lib/providers.ts).
  sttServerEngine: SttServerEngine | null;
  sttEnabled: boolean;
  // Whether this project prefers on-device (browser, WebGPU) transcription over sttApiKeyId/the
  // server's local Whisper — only shown/usable in the Configurator when the deployment also
  // allows it (see useBrowserSttStatus in lib/providers.ts).
  sttBrowserEnabled: boolean;
  // Whether the visitor's device speaks the replies (Pocket TTS in the browser), with the server's
  // TTS as the fallback — only offered when the deployment allows it and for its model's language
  // (see useBrowserTtsStatus in lib/providers.ts).
  ttsBrowserEnabled: boolean;
  // Whether the public chat should use sentence-chunked streaming (text+audio per sentence)
  // instead of waiting for the full reply — see backend features/chat/public_router.py's /messages/stream.
  // Meaningless without ttsEnabled, so the configurator only shows this toggle when TTS is on.
  streamingEnabled: boolean;
  // What the public chat page shows: avatar + chat (open or starting collapsed), the avatar alone
  // (voice only) or the chat alone (no 3D model loaded) — see pages/PublicChat/index.tsx.
  chatLayout: ChatLayout;
  // Whether the avatar uses body language: the LLM marks gestures in its replies, which the
  // vendored MotionEngine plays (see backend features/chat/motion.py). No effect with "chat_only".
  motionEnabled: boolean;
  // Random thinking gestures while the reply is generated (see TalkingHeadAvatar's startThinking).
  // Only has an effect together with motionEnabled.
  thinkingMotionsEnabled: boolean;
  // The teacher's own gesture instructions for the system prompt; null = the built-in default
  // (see backend features/chat/motion.py::effective_motion_prompt).
  motionPrompt: string | null;
  // Whether a visitor must enter a password before the public chat unlocks (see
  // pages/PublicChat/index.tsx). The password itself is write-only — set/change/remove it via
  // projectsApi.update's separate `chatPassword` field, never read back here.
  passwordProtected: boolean;
  // Whether a visitor must type a name or ID before the public chat starts (see
  // pages/PublicChat/index.tsx's "name-gate" stage) — the entered value shows up next to that
  // visitor's saved conversation in the analytics export, but only if saveConversations is also on.
  requireVisitorName: boolean;
  // Knowledge bases (see pages/Dashboard/Knowledge): how the avatar uses them, how many passages
  // it gets per turn, and which of the teacher's knowledge bases are attached.
  knowledgeMode: KnowledgeMode;
  knowledgeTopK: number;
  knowledgeBaseIds: string[];
  // Conversation plan: steps the avatar ticks off during the chat (see backend
  // features/chat/checklist.py) — optionally shown to students, in a fixed or any order.
  checklistEnabled: boolean;
  checklistVisible: boolean;
  checklistOrdered: boolean;
  checklistItems: ChecklistItem[];
  createdAt: string;
}

// One step of the conversation plan. The id stays the same when steps are reordered, so ticks
// in a running chat keep pointing at the right step; the note is only ever shown to the model.
export interface ChecklistItem {
  id: string;
  title: string;
  note: string | null;
}

// What projectsApi.update accepts: every writable field, plus the write-only chat password. The
// derived URLs (and llmModel) are computed server-side and never sent.
export type ProjectUpdate = Partial<
  Omit<Project, "avatarModelUrl" | "avatarBackgroundUrl" | "startAudioUrl" | "llmModel" | "passwordProtected">
> & { chatPassword?: string | null };

// The system prompt a chat turn really sends, in its parts (GET /projects/{id}/system-prompt).
export interface SystemPrompt {
  preprompt: string;
  motionPrompt: string;
  defaultMotionPrompt: string;
  motionCustomized: boolean;
  motionActive: boolean;
  // The plan as sent on a chat's first turn; null when the plan is off or empty.
  checklistPrompt: string | null;
  full: string;
}

export type SpokenLanguage = "de" | "en";

// "supplement": use the material where it helps; "strict": answer only from it.
export type KnowledgeMode = "off" | "supplement" | "strict";
export const KNOWLEDGE_MODES: KnowledgeMode[] = ["off", "supplement", "strict"];

// The local server STT engines (backend Settings.stt_engine / Project.stt_server_engine).
export type SttServerEngine = "whisper" | "parakeet";

export type ChatLayout = "avatar_chat" | "avatar_chat_collapsed" | "avatar_only" | "chat_only";
export const CHAT_LAYOUTS: ChatLayout[] = ["avatar_chat", "avatar_chat_collapsed", "avatar_only", "chat_only"];

export interface ProjectStats {
  totalProjects: number;
  publishedProjects: number;
  sessionsLast7Days: number;
  messagesLast7Days: number;
}
