"""
AI Provider Integrations

One package per capability, each behind a small interface with one module per provider:

- llm/  chat completion (litellm, GWDG Arcana)
- tts/  text-to-speech (litellm, Cartesia, Google Cloud TTS)
- stt/  speech-to-text (local Parakeet, GWDG SAIA)

Callers pick a client with get_llm_client/get_tts_client/get_stt_client for a stored API key and
never branch on the provider themselves. Combining the three (chat → speech) is the chat
feature's job, see app/features/chat/.
"""
