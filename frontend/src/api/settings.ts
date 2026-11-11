// API client for the public /settings/* backend routes: the unauthenticated view of the
// instance-wide site settings (contact email, whether self-registration is open).
import { apiClient } from "./client";

export interface PublicSiteSettings {
  contactEmail: string | null;
  contactPhone: string | null;
  providerName: string | null;
  providerStreet: string | null;
  providerCity: string | null;
  providerCountry: string | null;
  registrationEnabled: boolean;
  // Where the on-device speech models live, or null when the deployment doesn't offer them — the
  // student start page downloads them in advance (see lib/modelPrefetch.ts).
  browserSttModelUrl: string | null;
  browserTtsModelUrl: string | null;
}

export const settingsApi = {
  getPublic: () => apiClient.get<PublicSiteSettings>("/settings/public"),
};
