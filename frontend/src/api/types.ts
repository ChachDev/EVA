// Wire types mirror contracts/schemas/. Scores and review outcomes come from Backend.
export type Language = 'RU' | 'KZ' | 'EN';
export type Channel = 'agent_chat' | 'voice_call';
export type Speaker = 'client' | 'operator' | 'bot' | 'system' | 'unknown';
export type TranscriptRole = 'client' | 'operator';

export interface Message {
  id: string;
  speaker: Speaker;
  text: string;
  timestamp: string | null;
  offset_seconds: number | null;
  speaker_id?: string;
  event?: 'identity_verified';
  translation?: {language:string;text:string};
  end_seconds?: number | null;
  language?: Language | 'mixed' | 'unknown';
  quality?: string;
}

export interface Conversation {
  id: string;
  channel: Channel;
  language: Language;
  messages: Message[];
  audio_url: string | null;
  source?: {target_operator_id?: string; complete_start?: boolean; complete_end?: boolean; gaps?: boolean};
}

export interface ConversationCreate {
  operation_id: string;
  channel: Channel;
  language: Language;
  messages: Array<Omit<Message,'id' | 'offset_seconds'> & {offset_seconds?:number|null}>;
  source?: Conversation['source'];
}

export interface Criterion {
  id: string;
  title: string;
  instruction: string;
  depends_on?: string[];
  section_id: string | null;
  type: 'boolean' | 'scale' | 'flag';
  weight: number;
  allow_na: boolean;
  na_condition: string | null;
  scale: number[];
}

export interface Checklist {
  id: string;
  version: number;
  name: string;
  criteria: Criterion[];
}

export interface ChecklistPublish {
  base_version: number;
  name: string;
  criteria: Criterion[];
}

export interface Evidence {
  kind: 'message' | 'missing_action' | 'not_applicable' | 'uncertain';
  message_ids: string[];
}

export interface CriterionResult {
  criterion_id: string;
  status: 'needs_review' | 'scored';
  value: boolean | number | null;
  reason: string;
  evidence: Evidence[];
}

export interface EvaluationResponse {
  schema_version: '1.0.0';
  conversation_id: string;
  checklist_id: string;
  checklist_version: number;
  provider: string;
  model: string;
  criteria: CriterionResult[];
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

// evaluation-create: the browser selects stored inputs; Backend builds the ML request.
export interface EvaluationRequest {
  conversation_id: string;
  checklist_id: string;
  checklist_version: number;
}

// evaluation-request: the immutable full request saved alongside an evaluation.
export interface EvaluationSnapshot {
  schema_version: '1.0.0';
  conversation: Conversation;
  checklist: Checklist;
}

export interface Evaluation {
  id: string;
  status: 'needs_review' | 'completed';
  created_at: string;
  original: EvaluationResponse;
  scoring: ScoreSummary;
}

export interface EvaluationList {
  items: Evaluation[];
  next_cursor: string | null;
}

export interface EvaluationListQuery {
  conversation_id?: string;
  limit?: number;
  cursor?: string;
}

export interface AppealCreate {
  operation_id: string;
  expected_revision: number;
  criterion_id: string;
  reason: string;
  message_ids: string[];
}

type DecisionFields = {
  operation_id: string;
  expected_revision: number;
  reason: string;
};

export type AppealDecision = DecisionFields & (
  | { decision: 'accepted'; override: CriterionResult & { status: 'scored' } }
  | { decision: 'rejected'; override?: never }
);

export interface Appeal {
  id: string;
  evaluation_id: string;
  criterion_id: string;
  reason: string;
  message_ids: string[];
  created_at: string;
  status: 'open' | 'accepted' | 'rejected';
}

export interface ReviewDecision {
  id: string;
  appeal_id: string;
  evaluation_id: string;
  decision: 'accepted' | 'rejected';
  reason: string;
  override: CriterionResult | null;
  reviewer_id: string;
  created_at: string;
}

export interface ReviewCase {
  evaluation_id: string;
  revision: number;
  review_status: 'pending' | 'appealed' | 'resolved';
  snapshot_available: boolean;
  original: Evaluation;
  final: { criteria: CriterionResult[]; scoring: ScoreSummary };
  appeals: Appeal[];
  decisions: ReviewDecision[];
}

export type AudioContentType = 'audio/wav' | 'audio/mpeg' | 'audio/flac' | 'audio/ogg';

export interface AudioAsset {
  id: string;
  content_type: AudioContentType;
  size_bytes: number;
  sha256: string;
  duration_seconds: number | null;
  created_at: string;
}

export interface TranscriptionCreate {
  asset_id: string;
  language: Language;
  operation_id: string;
}

export interface TranscriptionResult {
  schema_version: '1.0.0';
  audio_sha256: string;
  language: Language;
  provider: string;
  model: string;
  segments: Array<{
    speaker: TranscriptRole | 'unknown';
    text: string;
    start_seconds: number;
    end_seconds: number;
  }>;
}

export interface TranscriptionJob {
  id: string;
  asset_id: string;
  language: Language;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'needs_review';
  created_at: string;
  updated_at: string;
  conversation_id: string | null;
  error_code: string | null;
  result: TranscriptionResult | null;
}

export interface TranscriptionReview {
  operation_id: string;
  roles: TranscriptRole[];
}

export interface BackendHealth {
  status: string;
  service?: 'backend';
  schema_version?: '1.0.0';
}

export interface AuditDetail {
  decision: boolean | number | null; status: string; action: string; uncertainty: string | null;
  evidence: Array<{utterance_id:string; quote:string; start:number|null}>;
}
export interface AuditExport {
  format:string; evaluation_id:string; review:ReviewCase; can_derive?:boolean;
  snapshot:EvaluationSnapshot|null;
  audit: null | {mode:string; status?:string; parent?:string; removed?:string[];
    changes?:Record<string,{fields:string[]; evaluate:boolean; differences?:Record<string,{from:unknown;to:unknown}>;before_decision?:CriterionResult|null;after_decision?:CriterionResult}>;
    from_version?:number;to_version?:number;review_projection?:{parent_revision:number;scoring:ScoreSummary};
    candidate?:{response:EvaluationResponse; audit:{criteria:Record<string,AuditDetail>;
      sentinel:Array<AuditDetail & {rule_id:string}>; rules:Record<string,{instruction:string}>}}};
}
export interface CqaApi {
  auditExport?(id:string): Promise<AuditExport>;
  deriveVersion?(id:string, version:number): Promise<Evaluation>;
  health(): Promise<BackendHealth>;
  getChecklists(): Promise<Checklist[]>;
  publishChecklist(id: string, payload: ChecklistPublish): Promise<Checklist>;
  getConversations(): Promise<Conversation[]>;
  getConversation(id: string): Promise<Conversation>;
  createConversation(payload: ConversationCreate): Promise<Conversation>;
  evaluate(payload: EvaluationRequest): Promise<Evaluation>;
  getEvaluationPage(query?: EvaluationListQuery): Promise<EvaluationList>;
  getEvaluations(conversationId?: string): Promise<Evaluation[]>;
  getEvaluation(id: string): Promise<Evaluation>;
  getEvaluationSnapshot(id: string): Promise<EvaluationSnapshot>;
  getReviewCase(evaluationId: string): Promise<ReviewCase>;
  createAppeal(evaluationId: string, payload: AppealCreate): Promise<ReviewCase>;
  decideAppeal(appealId: string, payload: AppealDecision): Promise<ReviewCase>;
  uploadAudio(blob: Blob, operationId: string, contentType?: AudioContentType | 'audio/x-wav'): Promise<AudioAsset>;
  getAudioAsset(id: string): Promise<AudioAsset>;
  audioContentUrl(id: string): string;
  createTranscription(payload: TranscriptionCreate): Promise<TranscriptionJob>;
  getTranscription(id: string): Promise<TranscriptionJob>;
  reviewTranscription(id: string, payload: TranscriptionReview): Promise<TranscriptionJob>;
}
