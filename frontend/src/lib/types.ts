export type FieldStatus = "verified" | "high_confidence" | "needs_review" | "unreadable" | "mismatch" | "missing";
export type FieldKind = "text" | "money" | "number" | "percent" | "date" | "unit" | "category" | "code" | "enum";
export type DocStatus = "queued" | "processing" | "review" | "ready" | "approved" | "failed";

export interface BBox {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Alternative {
  source: string;
  value: string | null;
  raw: string | null;
  note: string | null;
}

export interface ExtractedField {
  value: string | null;
  raw: string | null;
  extracted_value: string | null;
  kind: FieldKind;
  page: number | null;
  bbox: BBox | null;
  token_ids: string[];
  ocr_text: string | null;
  ocr_confidence: number | null;
  extraction_confidence: "high" | "medium" | "low" | null;
  sources: string[];
  alternatives: Alternative[];
  validated: boolean | null;
  status: FieldStatus;
  issues: string[];
  corrected: boolean;
  verified_by_user: boolean;
}

export interface PriceBookMatch {
  item_id: string;
  name: string;
  rate: string | null;
  currency: string | null;
  unit: string | null;
  score: number;
  reason: string;
}

export const LINE_KEYS = [
  "name",
  "description",
  "sku",
  "quantity",
  "unit",
  "rate",
  "discount",
  "tax_rate",
  "tax_amount",
  "amount",
  "line_total",
  "category",
] as const;
export type LineKey = (typeof LINE_KEYS)[number];

export type LineItem = {
  id: string;
  index: number;
  page: number | null;
  bbox: BBox | null;
  original_text: string | null;
  sources: string[];
  status: FieldStatus;
  issues: string[];
  computed_amount: string | null;
  pricebook_matches: PriceBookMatch[];
  user_added: boolean;
} & Record<LineKey, ExtractedField>;

export interface TaxLine {
  id: string;
  label: ExtractedField;
  tax_type: string | null;
  rate: ExtractedField;
  base: ExtractedField;
  amount: ExtractedField;
  applies_to: string[] | null;
  inclusive: boolean | null;
  status: FieldStatus;
  issues: string[];
}

export interface CurrencyInfo {
  code: string | null;
  symbol: string | null;
  confidence: "high" | "medium" | "low" | null;
  ambiguous: boolean;
  evidence: string[];
  candidates: { code: string; score: number }[];
  minor_units: number;
  status: FieldStatus;
  overridden: boolean;
  issues: string[];
}

export interface Suggestion {
  path: string;
  current: string | null;
  suggested: string;
  reason: string;
}

export interface ValidationCheck {
  id: string;
  category: "line" | "subtotal" | "tax" | "total" | "balance" | "consistency" | "format";
  label: string;
  expected: string | null;
  extracted: string | null;
  difference: string | null;
  status: "pass" | "explained" | "warning" | "fail" | "skipped";
  message: string;
  fields: string[];
  suggestions: Suggestion[];
}

export interface ValidationReport {
  status: "pass" | "warning" | "fail" | "incomplete" | "not_applicable";
  extracted_total: string | null;
  recalculated_total: string | null;
  difference: string | null;
  prices_include_tax: boolean | null;
  computed: Record<string, string | null>;
  checks: ValidationCheck[];
  reasons: string[];
}

export interface StageLog {
  stage: string;
  status: "ok" | "skipped" | "retry" | "warning" | "failed";
  started_at: string;
  duration_ms: number;
  detail: string;
  data: Record<string, unknown> | null;
}

export interface ReviewSummary {
  total_fields: number;
  high_confidence: number;
  verified: number;
  needs_review: number;
  unreadable: number;
  mismatch: number;
}

export interface PageQuality {
  page: number;
  text_source: "ocr" | "pdf_text" | "mixed";
  ocr_engine: string | null;
  token_count: number;
  mean_confidence: number | null;
  low_confidence_ratio: number | null;
  blur_score: number | null;
  warnings: string[];
}

export interface Extraction {
  schema_version: number;
  document_type: ExtractedField;
  language: string | null;
  currency: CurrencyInfo;
  header: Record<string, ExtractedField>;
  line_items: LineItem[];
  taxes: TaxLine[];
  summary: Record<string, ExtractedField>;
  prices_include_tax: boolean | null;
  validation: ValidationReport | null;
  review: ReviewSummary;
  extractors: string[];
  page_quality: PageQuality[];
  warnings: string[];
  notes: string[];
  approved: boolean;
}

export interface DocumentSummary {
  id: string;
  filename: string;
  source: "upload" | "camera";
  status: DocStatus;
  progress: number;
  progress_message: string | null;
  error: string | null;
  page_count: number;
  document_type: string | null;
  vendor_name: string | null;
  document_number: string | null;
  document_date: string | null;
  currency: string | null;
  grand_total: string | null;
  line_item_count: number;
  review_count: number;
  validation_status: string | null;
  extractors: string[];
  created_at: string | null;
  processed_at: string | null;
  approved_at: string | null;
  processing_ms: number | null;
  batch_id: string | null;
  thumb_url: string | null;
}

export interface PageInfo {
  page_no: number;
  width: number | null;
  height: number | null;
  text_source: string | null;
  source_kind: "image" | "pdf";
  quality: PageQuality | null;
  rotation: number;
  meta: { crop?: string; rotation?: number; deskew?: number; pdf_mode?: string; original_size?: [number, number] };
  image_url: string | null;
  original_url: string;
  thumb_url: string | null;
  tokens_url: string | null;
}

export interface DocumentDetail extends DocumentSummary {
  pages: PageInfo[];
  extraction: Extraction | null;
  pipeline_log: StageLog[];
  options: Record<string, unknown>;
}

export interface OcrToken {
  id: string;
  text: string;
  conf: number;
  bbox: [number, number, number, number];
  words: [string, number, number, number, number, number][];
  source: "ocr" | "pdf_text";
  noise: boolean;
  alt_text: string | null;
  alt_conf: number | null;
}

export interface PriceBookItem {
  id: string;
  name: string;
  description: string | null;
  sku: string | null;
  rate: string | null;
  currency: string | null;
  unit: string | null;
  unit_label: string | null;
  unit_display: string | null;
  category: string;
  tax_mode: "default" | "rate" | "not_taxable";
  tax_rate: string | null;
  vendor: string | null;
  source_document_id: string | null;
  created_at: string;
  updated_at: string;
  history?: { rate: string | null; currency: string | null; unit: string | null; note: string | null; source_document_id: string | null; recorded_at: string }[];
}

export interface ItemData {
  name: string;
  description: string | null;
  sku: string | null;
  rate: string | null;
  currency: string | null;
  unit: string | null;
  unit_label: string | null;
  category: string;
  tax_mode: string;
  tax_rate: string | null;
  vendor: string | null;
  source_document_id: string | null;
}

export interface StagedItem {
  key: string;
  data: ItemData;
  source_item_id: string | null;
  warnings: string[];
  matches: PriceBookMatch[];
  suggested_action: "create" | "update" | "skip";
  suggested_target: string | null;
}

export interface Dashboard {
  total_documents: number;
  processed: number;
  successful: number;
  needs_review: number;
  failed: number;
  in_progress: number;
  approved: number;
  line_items: number;
  price_book_items: number;
  avg_processing_ms: number | null;
  totals_by_currency: { currency: string; total: string }[];
  recent: DocumentSummary[];
  review_queue: DocumentSummary[];
}

export interface SystemInfo {
  version: string;
  ocr: { engine: string; model_size: string; engines: { name: string; available: boolean; description: string }[] };
  llm: { mode: string; active: string | null; providers: { name: string; model: string; configured: boolean }[] };
  limits: { max_upload_mb: number; max_files_per_request: number; max_pages_per_document: number; worker_concurrency: number };
  database: string;
  review_thresholds: { numeric: number; text: number };
}

export interface UserSettings {
  llm_mode: "always" | "fallback" | "off";
  date_order: "auto" | "DMY" | "MDY" | "YMD";
  display_currency: string | null;
  auto_crop: boolean;
  tax_presets: string[];
  custom_units: string[];
  categories: string[];
  default_currency_for_new_items: string | null;
}

export interface UnitOption {
  code: string;
  label: string;
  short: string;
  quick: boolean;
}

export interface Options {
  units: UnitOption[];
  currencies: { code: string; symbol: string; minor_units: number }[];
  document_types: string[];
}

export interface ExtractedRow {
  document_id: string;
  document_number: string | null;
  vendor: string | null;
  document_date: string | null;
  document_status: DocStatus;
  currency: string | null;
  item_id: string;
  name: string | null;
  description: string | null;
  sku: string | null;
  quantity: string | null;
  unit: string | null;
  rate: string | null;
  tax_rate: string | null;
  amount: string | null;
  category: string | null;
  status: FieldStatus;
}

// ---------------------------------------------------------------------------
// Clients + business-card scanner
// ---------------------------------------------------------------------------
export type ClientSource = "manual" | "contacts" | "business_card";

export interface Client {
  id: string;
  name: string;
  company: string | null;
  email: string | null;
  phone: string | null;
  job_title: string | null;
  website: string | null;
  street: string | null;
  city: string | null;
  state: string | null;
  zip_code: string | null;
  country: string | null;
  tax_id: string | null;
  source: ClientSource;
  created_at: string;
  updated_at: string;
}

export type ClientInput = {
  name: string;
  company?: string | null;
  email?: string | null;
  phone?: string | null;
  job_title?: string | null;
  website?: string | null;
  street?: string | null;
  city?: string | null;
  state?: string | null;
  zip_code?: string | null;
  country?: string | null;
  tax_id?: string | null;
  source?: ClientSource;
};

export type CardConfidence = "high" | "medium" | "low";

export interface CardScanResult {
  name: string | null;
  company: string | null;
  email: string | null;
  phone: string | null;
  job_title: string | null;
  website: string | null;
  tax_id: string | null;
  address: {
    street: string | null;
    city: string | null;
    state: string | null;
    zip_code: string | null;
    country: string | null;
  };
  other_emails: string[];
  other_phones: string[];
  /** Only for fields that have a value. Flat keys: name, company, email, phone, street, city, state, zip_code, country, website, job_title, tax_id */
  confidence: Record<string, CardConfidence>;
  missing_fields: string[];
  uncertain_fields: string[];
  warnings: string[];
  extractors: string[];
  sides_read: number;
  processing_ms: number;
}
