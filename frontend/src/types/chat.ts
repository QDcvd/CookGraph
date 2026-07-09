export interface RetrievedChunk {
  filename: string;
  page_number?: number;
  rrf_rank?: number;
  rerank_score?: number | null;
  text?: string;
}

export interface RagTrace {
  tool_used?: boolean;
  tool_name?: string;
  choice_prompt?: ChoicePrompt | null;
  hybrid_retrieval?: {
    strategy?: string;
    accepted?: boolean;
    skipped?: boolean;
    not_rewritten?: boolean;
    original_query?: string;
    standard_dish?: string;
    matched_text?: string;
    rewritten_query?: string;
    score?: number | null;
    margin?: number | null;
    candidates?: Array<{
      name: string;
      score?: number | null;
    }>;
    alias_debug?: string;
    lexical_debug?: string;
    dense_debug?: string;
    summary?: string;
  };
  retrieval_stage?: string;
  grade_score?: number;
  grade_route?: string;
  rewrite_needed?: boolean;
  rewrite_strategy?: string;
  rewrite_query?: string;
  retrieval_pipeline?: string;
  retrieval_mode?: string;
  candidate_k?: number;
  candidate_k_config_error?: string;
  candidate_k_source?: string;
  retrieval_candidate_multiplier?: number;
  recall_count?: number | null;
  post_merge_candidate_count?: number | null;
  candidate_count?: number | null;
  retrieval_top_k?: number;
  retrieved_chunks?: RetrievedChunk[];
  leaf_retrieve_level?: number;
  auto_merge_enabled?: boolean | null;
  auto_merge_applied?: boolean | null;
  auto_merge_threshold?: number;
  auto_merge_replaced_chunks?: number;
  auto_merge_steps?: number;
  rerank_enabled?: boolean | null;
  rerank_applied?: boolean | null;
  rerank_model?: string;
  rerank_error?: string;
  expansion_type?: string;
  step_back_question?: string;
  expanded_query?: string;
  hypothetical_doc?: string;
  complexity?: 'simple' | 'complex' | string;
  complexity_reason?: string;
  sub_questions?: string[];
  sub_agent_count?: number;
  synthesis_merged_count?: number;
  sub_traces?: any[];
  initial_retrieved_chunks?: RetrievedChunk[];
  expanded_retrieved_chunks?: RetrievedChunk[];
  token_usage?: TokenUsage | null;
}

export interface RagStep {
  key?: string;
  group?: string | null;
  label: string;
  icon?: string;
  detail?: string;
  status?: string;
  percent?: number;
  message?: string;
}

export interface GroupedRagStep {
  group: string | null;
  label: string | null;
  steps: RagStep[];
  collapsed: boolean;
}

export interface TokenUsage {
  completion_tokens_estimated: number;
  completion_chars?: number;
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_tokens?: number | null;
  source?: 'estimated' | 'provider' | 'mixed' | string;
  final?: boolean;
  model_rounds?: number;
}

export interface ChoicePromptOption {
  key: 'A' | 'B' | 'C' | string;
  label: string;
  send_text?: string;
  custom?: boolean;
}

export interface ChoicePrompt {
  id: string;
  type: string;
  question: string;
  options: ChoicePromptOption[];
  pending_type?: string;
  pending_payload?: Record<string, unknown>;
}

export interface Message {
  text: string;
  isUser: boolean;
  isThinking?: boolean;
  ragTrace?: RagTrace | null;
  ragSteps?: RagStep[];
  _groupedSteps?: GroupedRagStep[];
  tokenUsage?: TokenUsage | null;
  choicePrompt?: ChoicePrompt | null;
  selectedChoiceKey?: string | null;
}

export interface ChatSession {
  session_id: string;
  title?: string;
  message_count: number;
  updated_at: string;
}
