export interface RowItem {
  row_index: number;
  account_number: string;
  ch_code: string;
  contact_person: string;
  contact_relation: string;
  category_label: string;
  normalized_role: string;
  matched_relation_keyword?: string;
  concat_val: string;
  raw_remarks: string;
  cleaned_remarks: string;
  prohibited_tags_stripped: string[];
  trimmed_statement: string;
  original_char_count: number;
  final_char_count: number;
  truncated: boolean;
  predicted_csu: string;
  predicted_rfd: string;
  detailed_rfd: string;
  csu_reasoning: string;
  rfd_reasoning: string;
  csu_confidence: 'high' | 'medium' | 'low';
  csu_alternatives: string[];
  rfd_confidence: 'high' | 'medium' | 'low';
  rfd_alternatives: string[];
  manual_csu: string;
  manual_rfd: string;
  csu_match: boolean;
  rfd_match: boolean;
  discrepancy_flag: boolean;
  row_date: string;
  raw_message: string;
  ai_summarized: boolean;
  trim_method: string;
  detected_language: string;
  language_route: string;
  model_used: string;
  classification_source: string;
  prompt_feedback?: string;
  is_edited?: boolean;
}

export interface Taxonomies {
  csu_options: string[];
  rfd_options: string[];
  primary_csu: string[];
  primary_rfd: string[];
}

export interface ProcessResponse {
  success: boolean;
  total_rows: number;
  sanitized_tag_count: number;
  char_overflow_prevented_count: number;
  english_count: number;
  tagalog_count: number;
  detection_latency_ms: number;
  ai_summarized_count: number;
  rule_based_fallback_count: number;
  model_tagalog: string;
  model_english: string;
  rows: RowItem[];
}

export interface TestResponse extends ProcessResponse {
  csu_accuracy_pct: number;
  rfd_accuracy_pct: number;
  discrepancy_count: number;
  rows_with_ground_truth: number;
  representative_count: number;
  informant_count: number;
  cardholder_count: number;
}

export interface PromptProfile {
  id: string;
  name: string;
  description: string;
  model_english: string;
  model_tagalog: string;
  system_instructions: string;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProfilesData {
  active_profile_id: string;
  profiles: PromptProfile[];
}
