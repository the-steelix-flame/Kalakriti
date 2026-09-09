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
const get = <T,>(p: string, ms?: number) => call<T>(p, undefined, ms);

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
export const listListings = () =>
  get<{ listings: Listing[]; cards: Card[] }>('/v1/listings');
/**
 * Save an edit. `baseUpdatedAt` is the version of the row the artisan was looking
 * at; the server uses it to detect that the row moved while this edit sat in the
 * offline queue, and returns per-field conflicts instead of overwriting blindly.
 */
export const updateListing = (
  id: string,
  body: Partial<Listing> & { baseUpdatedAt?: string | null },
) => patch<Listing & { conflicts?: { field: string; mine: any; theirs: any }[] }>(
  `/v1/listings/${id}`, body);

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

/* ───────────────────────────────────── product cards, marketplaces, insight */

/**
 * One metric from one marketplace.
 *
 * `available: false` is a real answer, not an error. Amazon does not give a seller
 * application a per-listing view count and ONDC has no view concept at all, so those
 * arrive with `available: false` and a `why` the app shows verbatim. Rendering a zero
 * instead would tell the artisan nobody looked, which is a different and false claim.
 */
export type Metric = {
  value: number | null;
  available: boolean;
  /** Written for the artisan: what this platform does or does not share. */
  why: string;
  /** Operator-facing: the exact environment variables still needed, if any. */
  config?: string;
  source: string;
};

export type Stats = Record<'views' | 'watchers' | 'sold' | 'inventory' | 'revenue'
                           | 'orders', Metric>;

/** A row of My Products: counted from real rows, never estimated. */
export type Card = {
  id: string;
  title: string; titleEn: string;
  imageUrl: string;
  price: number; currency: string; quantity: number; category: string;
  status: string;            // includes derived states: partial, failed
  rawStatus: string;
  marketplaces: number; marketplacesAttempted: number;
  channels: string[]; failedChannels: string[];
  views: number | null; viewsAvailable: boolean;
  orders: number; sold: number; revenue: number;
  updatedAt?: string; createdAt?: string;
};

export type MarketplaceRow = Publication & {
  channelName: string;
  supports: Record<string, boolean>;
  localOrders: { orders: number; sold: number; revenue: number;
                 pending: number; toShip: number };
  orderCount: number;
  stats?: Stats;
};

export type ProductDetail = {
  listing: Listing;
  card: Card;
  marketplaces: MarketplaceRow[];
  orders: Order[];
  events: any[];
};

export type Summary = {
  authenticated: boolean;
  drafts?: Card[];
  products?: { total: number; byStatus: Record<string, number>; live: number;
               drafts: number; sold: number };
  orders?: { orders: number; sold: number; revenue: number; pending: number;
             toShip: number };
  storefrontViews?: number;
  newEnquiries?: number;
  actions?: { kind: string; listingId?: string; orderId?: string; title: string;
              image?: string; detail?: string; amount?: number }[];
  actionCount?: number;
  watched?: Card[];
  best?: Card[];
};

export type Insights = {
  enough: boolean;
  minCohort: number;
  categories: { category: string; listings: number; artisans: number;
                priceLow: number; priceHigh: number; median: number;
                yours: boolean }[];
  basis: string;
  note: string;
};

export type Enquiry = {
  id: string; listingId: string; channel: string;
  buyerName: string; buyerPhone: string; buyerEmail: string; organisation: string;
  quantity: number; targetPrice: number; neededBy: string; message: string;
  status: string; reply: string;
  productTitle?: string;
  createdAt?: string; updatedAt?: string;
};

export const summary = () => get<Summary>('/v1/summary');
export const insights = () => get<Insights>('/v1/insights');
export const productDetail = (id: string) => get<ProductDetail>(`/v1/listings/${id}/detail`);
export const marketplaceDetail = (id: string, channel: string) =>
  get<{ listingId: string; marketplace: MarketplaceRow; orders: Order[]; events: any[] }>(
    `/v1/listings/${id}/marketplaces/${channel}`, 120000);

export const listEnquiries = () => get<{ enquiries: Enquiry[] }>('/v1/enquiries');
export const replyEnquiry = (id: string, body: { reply?: string; status?: string }) =>
  patch<Enquiry>(`/v1/enquiries/${id}`, body);

/* ────────────────────────────────── slow connections: jobs and field assist */

export type Job = {
  id: string;
  kind: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled';
  stage: string;
  progress: number;
  listingId: string;
  error: string;
  seen: boolean;
  createdAt?: string; updatedAt?: string; finishedAt?: string;
  result?: {
    listingId: string;
    titleEn: string; titleHi: string; descEn: string;
    category: string; hsn: string;
    price: number; floorPrice: number;
    attributes: Record<string, any>;
    ocrText: string;
    confidence: Record<string, number>;
    suggestions: Record<string, any>;
    notes: string;
    thumbUrl: string; imageUrl: string;
    ms: number;
  };
};

/**
 * Hand the server a photograph and stop waiting.
 *
 * Returns in well under a second. The artisan can close the app; the work carries on
 * without her. This is the path that survives a connection which cannot hold a
 * five-minute request open, which is most of them.
 */
export const createJob = (b: {
  imageBase64: string; transcript?: string; lang?: string;
  background?: string; listingId?: string | null;
}) => post<Job>('/v1/jobs', b, 120000);

/** Deliberately small: this is polled, sometimes on a connection billed by the MB. */
export const listJobs = () =>
  get<{ jobs: Job[]; working: number; ready: number }>('/v1/jobs');

export const getJob = (id: string) => get<Job>(`/v1/jobs/${id}`);
export const markJobSeen = (id: string) => post<{ ok: boolean }>(`/v1/jobs/${id}/seen`, {});

export type AssistField =
  | 'title' | 'titleHi' | 'description' | 'descriptionHi'
  | 'category' | 'hsn' | 'tags';

/**
 * Fill one field with AI, on request.
 *
 * The result is returned, never written: nothing changes on the listing until the
 * artisan accepts it. That is what makes manual mode a choice rather than a lesser
 * version of the app - every model is still here, she decides which and when.
 */
export const assist = (b: { listingId: string; field: AssistField;
                            instruction?: string }) =>
  post<{ field: string; value: any; note: string; source: string }>(
    '/v1/assist', b, 300000);

/** "Carry on with AI from here" - the remaining steps, as suggestions. */
export const assistContinue = (b: { listingId: string; from_step?: string }) =>
  post<{ listingId: string; suggestions: Record<string, any>; note: string }>(
    '/v1/assist/continue', b, 600000);
