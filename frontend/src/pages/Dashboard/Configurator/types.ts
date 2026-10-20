import type { ChatLayout, ChecklistItem, KnowledgeMode, SpokenLanguage, SttServerEngine, ThinkingMode } from "@/types/project";

// Local wizard state for screen 1e.
export interface ConfiguratorDraft {
  title: string;
  description: string;
  // See Project in types/project.ts — library avatar, bundled avatar, or neither (default).
  avatarModelId: string | null;
  builtinAvatar: string | null;
  avatarBackgroundId: string | null;
  chatLayout: ChatLayout;
  motionEnabled: boolean;
  thinkingMotionsEnabled: boolean;
  gradeLevel: string;
  preprompt: string;
  startPrompt: string;
  // The model is chosen via the configured key (screen 1g); null = none chosen yet.
  llmApiKeyId: string | null;
  temperature: number;
  topP: number;
  thinkingMode: ThinkingMode;
  saveConversations: boolean;
  requireVisitorName: boolean;
  surveyBeforeUrl: string;
  surveyBeforeEnabled: boolean;
  surveyAfterUrl: string;
  surveyAfterEnabled: boolean;
  spokenLanguage: SpokenLanguage;
  ttsEnabled: boolean;
  ttsApiKeyId: string | null;
  ttsVoice: string;
  ttsVoiceClipId: string | null;
  sttApiKeyId: string | null;
  sttServerEngine: SttServerEngine | null;
  sttEnabled: boolean;
  sttBrowserEnabled: boolean;
  ttsBrowserEnabled: boolean;
  streamingEnabled: boolean;
  knowledgeMode: KnowledgeMode;
  knowledgeTopK: number;
  knowledgeBaseIds: string[];
  checklistEnabled: boolean;
  checklistVisible: boolean;
  checklistOrdered: boolean;
  checklistItems: ChecklistItem[];
}

export interface StepProps {
  draft: ConfiguratorDraft;
  onChange: (patch: Partial<ConfiguratorDraft>) => void;
}
