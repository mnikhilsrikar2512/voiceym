import { apiJson } from './client';

export type AgentModel = { name: string; size: number };

export type AgentModels = {
  /** Ollama answered on its local port. */
  available: boolean;
  /** Installed models, smallest first. */
  models: AgentModel[];
  /** The lightest preferred model that is installed. */
  default: string;
};

export async function getAgentModels(): Promise<AgentModels> {
  return apiJson<AgentModels>('/v1/agent/models');
}
