declare const __MERCHANT_ID__: string
const API_BASE = '/server'
export interface Evidence { evidence_id:string; kind:'razorpay_fact'|'sandbox_metric'; summary:string; source_reference:string|null; details:Record<string,unknown>; created_at:string }
export interface Incident { incident_id:string;merchant_id:string;incident_type:string;status:string;currency:string;method:string;bank:string|null;error_reason:string;detector_version:string;baseline_window_start:string;current_window_start:string;current_window_end:string;baseline_attempt_count:number;baseline_failure_count:number;current_attempt_count:number;current_failure_count:number;baseline_failure_rate:number;current_failure_rate:number;revenue_at_risk_subunits:number;confidence:number;opened_at:string;last_detected_at:string;evidence:Evidence[] }
export interface Proposal { proposal_id:string;status:string;actions:Array<{action_id:string;payment_id:string;action_type:string;amount_subunits:number;rationale:string}>;total_amount_subunits:number;expires_at:string;policy_allowed:boolean;policy_version:string;eligible_payment_count?:number;omitted_payment_count?:number;stopping_conditions?:string[] }
export interface AuditEvent { audit_id:string;event_type:string;occurred_at:string;actor_type?:string;actor_id?:string|null;details?:Record<string,unknown> }
export interface CommanderReply { answer:string;confirmed_facts:string[];hypotheses:string[];suggested_prompts:string[];evidence_count:number;evidence_verified:boolean;safety_notice:string }
export interface CurrencyAmount { currency:string;amount_subunits:number }
export interface DashboardSummary { total_payment_attempts:number;captured_payment_count:number;captured_revenue_today:CurrencyAmount[];total_incident_count:number;open_incident_count:number;open_revenue_at_risk:CurrencyAmount[];reporting_timezone:string;reporting_day:string;generated_at:string }
async function request<T>(path:string,options?:RequestInit):Promise<T>{if(!__MERCHANT_ID__)throw new Error('MERCHANT_ID is missing from the repository .env file.');const response=await fetch(`${API_BASE}${path}`,{...options,headers:{'Content-Type':'application/json','X-Merchant-Id':__MERCHANT_ID__,...options?.headers}});if(!response.ok){const payload=await response.json().catch(()=>null);throw new Error(payload?.detail??`Request failed with status ${response.status}`)}return response.json() as Promise<T>}
export const getDashboardSummary=()=>request<DashboardSummary>('/dashboard/summary')
export const getIncidents=()=>request<Incident[]>('/incidents?limit=100')
export const getIncident=(id:string)=>request<Incident>(`/incidents/${id}`)
export const getIncidentAudit=(id:string)=>request<AuditEvent[]>(`/incidents/${id}/audit`)
export const getActiveProposal=(id:string)=>request<Proposal|null>(`/incidents/${id}/proposal`)
export const askIncidentCommander=(id:string,message:string)=>request<CommanderReply>(`/incidents/${id}/commander/chat`,{method:'POST',body:JSON.stringify({message})})
export const decideProposal=(id:string,action:'approve'|'reject',reason?:string)=>request<{decision:string}>(`/proposals/${id}/${action}`,{method:'POST',body:JSON.stringify({decided_by:'demo-merchant-operator',reason})})
