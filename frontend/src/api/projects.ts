import { apiClient, downloadFile } from "./client";
import i18n from "@/i18n";
import type { ChatMessage, ChecklistCheck } from "@/types/chat";
import type { Project, ProjectStats, ProjectUpdate, SystemPrompt } from "@/types/project";

export const projectsApi = {
  list: () => apiClient.get<Project[]>("/projects"),
  stats: () => apiClient.get<ProjectStats>("/analytics/overview"),
  create: () => apiClient.post<Project>("/projects", { title: i18n.t("configurator.newProject") }),
  get: (id: string) => apiClient.get<Project>(`/projects/${id}`),
  systemPrompt: (id: string) => apiClient.get<SystemPrompt>(`/projects/${id}/system-prompt`),
  // chatPassword isn't part of Project (write-only, see types/project.ts) — null clears/disables
  // the chat password, a non-empty string sets/changes it, omitted leaves it unchanged.
  update: (id: string, data: ProjectUpdate) =>
    apiClient.put<Project>(`/projects/${id}`, data),
  // Also deletes the project's saved conversations and access logs, server-side.
  remove: (id: string) => apiClient.delete<void>(`/projects/${id}`),
  publish: (id: string) => apiClient.put<Project>(`/projects/${id}/publication`),
  unpublish: (id: string) => apiClient.delete<Project>(`/projects/${id}/publication`),
  // Synthesizes and stores the project's startPrompt as audio (once) — see the "Generate audio"
  // button in Step3Behavior.
  generateStartAudio: (id: string) => apiClient.post<Project>(`/projects/${id}/start-audio`),
  previewMessage: (id: string, message: string, history: ChatMessage[]) =>
    apiClient.post<{
      reply: string;
      audioBase64: string | null;
      contentType: string | null;
      motions: string[];
      checks: ChecklistCheck[];
    }>(
      `/projects/${id}/chat/messages`,
      { message, history },
    ),
  transcribe: (id: string, audio: Blob) => {
    const formData = new FormData();
    formData.append("audio", audio, "recording.webm");
    return apiClient.upload<{ text: string }>(`/projects/${id}/chat/transcriptions`, formData);
  },
  // Downloads a project's configuration as a .yml file.
  exportYaml: (id: string, fallbackFilename: string) => downloadFile(`/projects/${id}/export`, fallbackFilename),
  // Creates a new draft project from a previously exported .yml file.
  importYaml: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.upload<Project>("/projects/import", formData);
  },
};
