// Local view types only. contracts/ is the shared, read-only source of truth.
export interface Conversation {
  id: string;
  channel: 'agent_chat' | 'voice_call';
  language: 'RU' | 'KZ' | 'EN';
  messages: Array<{
    id: string;
    speaker: 'client' | 'operator' | 'bot' | 'system';
    text: string;
    timestamp: string;
    offset_seconds: number | null;
  }>;
  audio_url: string | null;
}

export interface Checklist {
  id: string;
  version: number;
  name: string;
  criteria: Array<{
    id: string;
    title: string;
    instruction: string;
    section_id: string | null;
    type: 'boolean' | 'scale' | 'flag';
    weight: number;
    allow_na: boolean;
    na_condition: string | null;
    scale: number[];
  }>;
}

export interface EvaluationResponse {
  schema_version: '1.0.0';
  conversation_id: string;
  checklist_id: string;
  checklist_version: number;
  provider: string;
  model: string;
  criteria: Array<{
    criterion_id: string;
    status: 'needs_review' | 'scored';
    value: boolean | number | null;
    reason: string;
    evidence: Array<{
      kind: 'message' | 'missing_action' | 'not_applicable' | 'uncertain';
      message_ids: string[];
    }>;
  }>;
  warnings: string[];
}

export interface ScoreSummary {
  status: 'complete' | 'needs_review' | 'not_calculable';
  total: number | null;
  applicable_weight: number;
  has_gross_error: boolean | null;
  sections: Array<{ section_id: string; score: number | null }>;
  criteria: Array<{
    criterion_id: string;
    points: number | null;
    effective_weight: number | null;
  }>;
}

export interface EvaluationRequest {
  conversation_id: string;
  checklist_id: string;
  checklist_version: number;
}

export interface Evaluation {
  id: string;
  status: 'needs_review' | 'completed';
  created_at: string;
  original: EvaluationResponse;
  scoring: ScoreSummary;
}

export interface CqaApi {
  health(): Promise<{ status: string }>;
  getChecklists(): Promise<Checklist[]>;
  getConversations(): Promise<Conversation[]>;
  evaluate(request: EvaluationRequest): Promise<Evaluation>;
}
