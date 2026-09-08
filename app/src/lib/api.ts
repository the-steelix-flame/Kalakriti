/**
 * Backend client.
 *
 * Every call hits the real FastAPI service. There are no local fallbacks here on
 * purpose: if the backend is down the UI must say so, not invent a product. The
 * previous version's silent mock fallback is exactly how a photograph of a clay pot
 * came back as a Banarasi dupatta.
 */
import * as session from './session';
import { apiBase, hasBackend } from './config';

/** Kept as a getter: the base can change at runtime from Settings. */
export const API = { get url() { return apiBase(); } };

/** Raised when there is no backend to call at all, so callers can go offline-first. */
export class OfflineError extends Error {
  constructor() { super('backend not reachable'); }
}

/** Thrown with the server's structured detail intact, so callers can react to 401/428. */
export class ApiError extends Error {
  status: number;
  detail: any;
  constructor(status: number, detail: any, message: string) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function call<T>(path: string, init?: RequestInit, timeoutMs = 300000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  const token = session.getToken();
  if (!hasBackend()) {
    clearTimeout(timer);
    throw new OfflineError();
  }
  try {
    const r = await fetch(`${apiBase()}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        'X-Guest-Token': session.guestToken(),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init?.headers || {}),
      },
      signal: ctrl.signal,
    });
    const text = await r.text();
    let json: any = {};
    try { json = text ? JSON.parse(text) : {}; } catch { json = { detail: text }; }
    if (!r.ok) {
      const d = json?.detail;
      const msg = typeof d === 'string' ? d : (d?.message || `HTTP ${r.status}`);
      throw new ApiError(r.status, d, msg);
    }
    return json as T;
  } finally {
    clearTimeout(timer);
  }
}

const post = <T,>(p: string, body: any, ms?: number) =>
  call<T>(p, { method: 'POST', body: JSON.stringify(body) }, ms);
const patch = <T,>(p: string, body: any) =>
  call<T>(p, { method: 'PATCH', body: JSON.stringify(body) });
const get = <T,>(p: string) => call<T>(p);

/* --------------------------------------------------------------- types */

export type OcrBox = { text: string; confidence: number; box: number[] };

export type Listing = {
  id: string;
  titleEn: string; titleHi: string;
  descEn: string; descHi: string;
  category: string; hsn: string;
  price: number; floorPrice: number; quantity: number;
  attributes: Record<string, any>;
  tags: string[];
  imageUrl: string; rawHash: string; enhanceOps: string[];
  vision: any; ocr: { boxes?: OcrBox[]; text?: string };
  transcript: string; status: string;
  channelsSelected: string[];
  artisanId?: string | null;
  weightG?: number; lengthCm?: number; breadthCm?: number; heightCm?: number;
  publications: Publication[];
  orders: Order[];
  createdAt?: string; updatedAt?: string;
};

export type Publication = {
  id: string; channel: string; status: string; externalId: string;
  url: string; error: string; submittedAt?: string; updatedAt?: string;
};

export type Order = {
  id: string; listingId: string; channel: string; buyerName: string;
  buyerPhone: string; buyerEmail: string; address: string; quantity: number;
  amount: number; status: string; paymentStatus: string; paymentProvider: string;
  courier: string; trackingId: string; trackingUrl: string; createdAt?: string;
};

export type AnalyzeOut = {
  listingId: string; imageUrl: string; rawHash: string;
  ops: string[]; ms: number;
  ocr: { ok: boolean; boxes: OcrBox[]; text: string; engine?: string };
  detected: Record<string, any>;
  suggestions: Record<string, any>;
  confidence: Record<string, number>;
  notes: string; ocrUsed: string[]; source: string;
  listing: Listing;
};

export type Channel = {
  id: string; name: string; note: string; configured: boolean; missing: string[];
};

export type PriceAdvice = {
  suggested: number; floor: number; range: [number, number];
  comps: { title: string; price: number; source: string }[];
  breakdown: { label: string; amount: number }[];
  rationale: string; underpricedWarning?: string; source: string;
};

/* ------------------------------------------------------------- endpoints */

export const health = () => get<any>('/health');
export const channels = () => get<{ channels: Channel[] }>('/v1/channels');

/** Reads any local/blob/data URI into bare base64 for JSON transport. */
export async function toBase64(uri: string): Promise<string> {
  if (uri.startsWith('data:')) return uri.split(',')[1];
  const blob = await (await fetch(uri)).blob();
  return await new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onerror = () => reject(fr.error);
    fr.onload = () => resolve(String(fr.result).split(',')[1]);
    fr.readAsDataURL(blob);
  });
}

/** enhance -> OCR -> detect -> matte -> background generation -> draft row. */
export async function analyze(uri: string, opts: {
  transcript?: string; lang?: string; background?: string; listingId?: string;
} = {}): Promise<AnalyzeOut> {
  const imageBase64 = await toBase64(uri);
  return post<AnalyzeOut>('/v1/analyze', {
    imageBase64,
    transcript: opts.transcript ?? '',
    lang: opts.lang ?? 'hi-IN',
    background: opts.background ?? 'studio',
    listingId: opts.listingId ?? null,
  }, 600000);
}

export const catalog = (b: {
  transcript?: string; lang?: string; detected?: any; ocrText?: string; listingId?: string;
}) => post<any>('/v1/catalog', b, 600000);

export const suggestPrice = (b: {
  catalog?: any; detected?: any; materialCost?: number; days?: number; listingId?: string;
}) => post<PriceAdvice>('/v1/price', b, 600000);

export const getListing = (id: string) => get<Listing & { events: any[] }>(`/v1/listings/${id}`);
export const listListings = () => get<{ listings: Listing[] }>('/v1/listings');
export const updateListing = (id: string, body: Partial<Listing>) =>
  patch<Listing>(`/v1/listings/${id}`, body);

export const publishListing = (id: string, chans: string[]) =>
  post<{ listing: Listing; publications: Publication[] }>(
    `/v1/listings/${id}/publish`, { channels: chans }, 300000);

export const listingStatus = (id: string) =>
  get<{ id: string; status: string; flow: string[]; publications: Publication[];
        orders: Order[]; events: any[] }>(`/v1/listings/${id}/status`);

export const listOrders = () => get<{ orders: Order[] }>('/v1/orders');
export const updateOrder = (id: string, body: any) => patch<Order>(`/v1/orders/${id}`, body);

export const mintPassport = (b: { rawHash: string; ops: string[]; artisanId: string }) =>
  post<any>('/v1/passport', b);

export const trends = (cluster: string) =>
  post<{ trends: any[]; source: string }>('/v1/trends', { cluster }, 300000);


/* ─────────────────────────────────────────────── auth, profile, logistics */

export type Address = {
  id?: string; kind: string; contactName: string; contactPhone: string;
  line1: string; line2: string; landmark: string; city: string; state: string;
  pincode: string; country: string; complete?: boolean;
};

export type Artisan = {
  id: string; phone: string; phoneVerified: boolean; fullName: string;
  businessName: string; email: string; language: string; cluster: string;
  gstin: string; pan: string; bankAccount: string; bankIfsc: string;
  addresses: Address[]; accounts: any[];
};

export type Check = {
  field: string; ok: boolean; why: string; whyEn: string; required: boolean;
};
export type Readiness = {
  channel: string; ready: boolean; loggedIn: boolean;
  missing: string[]; missingRecommended: string[]; checks: Check[];
};

export type Bootstrap = {
  authenticated: boolean;
  artisan: Artisan | null;
  language: string | null;
  drafts: Listing[];
  otpDelivery: string;
  otpDevEcho: boolean;
  logistics: { provider: string; configured: boolean; missing: string[] };
  marketplaceReadiness: Readiness[];
};

export const bootstrap = () => get<Bootstrap>('/v1/bootstrap');

export const requestOtp = (phone: string) =>
  post<{ ok: boolean; challengeId?: string; delivery?: string; devCode?: string;
         devNotice?: string; message?: string; error?: string; retryAfter?: number }>(
    '/v1/auth/otp/request', { phone }, 60000);

export const verifyOtp = (challengeId: string, code: string, guestToken: string) =>
  post<{ ok: boolean; token: string; artisan: Artisan; isNewAccount: boolean;
         claimedDrafts: number }>('/v1/auth/otp/verify',
    { challengeId, code, guestToken }, 60000);

export const me = () => get<{ artisan: Artisan; marketplaceReadiness: Readiness[] }>(
  '/v1/auth/me');
export const logout = () => post<{ ok: boolean }>('/v1/auth/logout', {});

export const updateProfile = (b: Partial<{
  fullName: string; businessName: string; email: string; language: string;
  gstin: string; pan: string; bankAccount: string; bankIfsc: string;
}>) => patch<{ artisan: Artisan; marketplaceReadiness: Readiness[] }>('/v1/profile', b);

export const saveAddress = (b: Partial<Address> & { copyToReturn?: boolean }) =>
  call<{ artisan: Artisan; marketplaceReadiness: Readiness[] }>(
    '/v1/profile/address', { method: 'PUT', body: JSON.stringify(b) });

export const readiness = () =>
  get<{ loggedIn: boolean; readiness: Readiness[] }>('/v1/marketplaces/readiness');

export const mapping = (channel: string) =>
  get<{ channel: string; readiness: Readiness; mapped: any }>(
    `/v1/marketplaces/${channel}/mapping`);

export const shipOrder = (oid: string) =>
  post<{ shipment: any; order: Order }>(`/v1/orders/${oid}/ship`, {}, 120000);
