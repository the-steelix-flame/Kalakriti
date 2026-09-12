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
      // `why` is the cluster endpoints' field, and it is written to be read aloud to
      // an artisan rather than logged. Preferring it over a bare status code is the
      // difference between "HTTP 409" and "you have 10 units free and this needs 30".
      const msg = typeof d === 'string'
        ? d
        : (d?.why || d?.message || `HTTP ${r.status}`);
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
  /** The photograph as taken, kept beside the enhanced one for the passport page. */
  rawUrl?: string;
  /** Null until a photograph has been analysed, which is what mints the passport. */
  passport?: {
    id: string; signature: string; publicKey: string;
    payload: string; mintedAt: string | null;
  } | null;
  vision: any; ocr: { boxes?: OcrBox[]; text?: string };
  transcript: string; status: string;
  channelsSelected: string[];
  /** Which cluster sells this product, or '' for the artisan's own storefront. The
   *  cluster owner is the seller of record - they hold the GSTIN - so this is the
   *  link that lets an artisan without GST reach a marketplace at all. */
  clusterId?: string;
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
  /** The photograph as it was taken, kept so the passport page can show both. */
  rawUrl?: string;
  /** Signed on the server as part of the analysis - no second call to mint it. */
  passport?: Passport;
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

/**
 * Mint the Provenance Passport for a listing.
 *
 * `listingId` is what makes the passport survive the response that created it: the
 * server stores it on the listing and serves `verifyUrl` publicly, which is the URL
 * the QR code encodes. Without it the passport is signed and immediately lost.
 */
export const mintPassport = (b: {
  rawHash: string; ops: string[]; artisanId: string; listingId?: string;
}) => post<Passport>('/v1/passport', b);

export type Passport = {
  id: string;
  rawHash: string;
  enhanceOps: string[];
  artisanId: string;
  giTag: string | null;
  geo: string | null;
  capturedAt: string;
  signature: string;
  publicKey: string;
  listingId: string | null;
  stored: boolean;
  /** The page a plain camera app opens when it scans the QR code. */
  verifyUrl: string;
};

export const trends = (cluster: string) =>
  post<{ trends: any[]; source: string }>('/v1/trends', { cluster }, 300000);


/* ─────────────────────────────────────────────── auth, profile, logistics */

export type Role = 'artisan' | 'solo_seller' | 'cluster_creator';

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
  /**
   * Which of the three user types this is. `artisan` is the default and the only
   * one that requires no paperwork, so a new signup is never blocked on documents
   * they do not have.
   */
  role: Role;
  /** Pieces per cycle, and how many of them are already promised. One pair per
   *  person, not per cluster - that is what stops the same weeks being sold twice. */
  capacityUnits: number;
  capacityCommitted: number;
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

/* ------------------------------------------------------------- clusters */

export type ClusterRating = {
  available: boolean; count: number; why: string;
  overall: number | null; paidOnTime: number | null;
  commissionFair: number | null; ordersRegular: number | null;
};

export type ClusterPlatform = {
  channel: string; name: string; live: boolean; why: string;
};

export type ClusterPayouts = {
  available: boolean; count: number; why: string;
  averagePerArtisan: number | null; totalPaid: number | null;
};

export type Membership = {
  id: string; clusterId: string; artisanId: string;
  status: 'active' | 'left'; joinedAt: string | null; leftAt: string | null;
};

export type ClusterMember = Membership & {
  name: string; phone: string;
  capacityUnits: number; capacityAvailable: number;
};

export type Cluster = {
  id: string; ownerArtisanId: string; name: string; craftCategory: string;
  district: string; state: string;
  commissionPct: number; maxOrderUnits: number;
  inviteCode: string; status: string; memberCount: number;
  createdAt: string | null;
  ownerName: string; ownerGstinLast4: string;
  capacityAvailableUnits: number;
  rating: ClusterRating;
  platforms: ClusterPlatform[];
  payouts: ClusterPayouts;
  viewerMembership: Membership | null;
  viewerIsOwner: boolean;
  /** Owner-only, returned by getCluster when the caller owns it. */
  members?: ClusterMember[];
  /** Present on the `mine=true` listing. */
  membership?: Membership;
};

export type Capacity = {
  artisanId: string; capacityUnits: number;
  capacityCommitted: number; capacityAvailable: number;
};

/** Open clusters, filterable. Works signed out - terms are readable before joining. */
export const browseClusters = (p: { craftCategory?: string; district?: string;
                                    state?: string; q?: string } = {}) => {
  const qs = new URLSearchParams(
    Object.entries(p).filter(([, v]) => !!v) as [string, string][]).toString();
  return get<{ clusters: Cluster[] }>(`/v1/clusters${qs ? `?${qs}` : ''}`);
};

/** The caller's own memberships, plus any clusters they own. */
export const myClusters = () =>
  get<{ clusters: Cluster[]; owned: Cluster[] }>('/v1/clusters?mine=true');

export const getCluster = (id: string) => get<Cluster>(`/v1/clusters/${id}`);

/**
 * Resolve an invite code WITHOUT joining.
 *
 * Deliberately a separate call from join: scanning a QR code should show somebody
 * the commission and the platforms they are about to sell under, not enrol them.
 */
export const clusterByCode = (code: string) =>
  get<Cluster>(`/v1/clusters/by-code/${encodeURIComponent(code.trim())}`);

export const createCluster = (b: {
  name: string; craftCategory?: string; commissionPct: number;
  maxOrderUnits?: number; district?: string; state?: string;
}) => post<Cluster>('/v1/clusters', b);

export const joinCluster = (id: string) =>
  post<{ membership: Membership; cluster: Cluster }>(`/v1/clusters/${id}/join`, {});

export const joinClusterByCode = (inviteCode: string) =>
  post<{ membership: Membership; cluster: Cluster }>(
    '/v1/clusters/join', { inviteCode: inviteCode.trim() });

export const leaveCluster = (id: string) =>
  post<Membership>(`/v1/clusters/${id}/leave`, {});

/** The Solo Seller / Cluster Creator switch. Changeable at any time, both ways. */
export const setRole = (role: Role) => patch<Artisan>('/v1/me/role', { role });

/** How many units this artisan can make in a cycle. One number per person. */
export const setCapacity = (units: number) =>
  patch<Artisan>('/v1/me/capacity', { units });

export const commitCapacity = (b: { units: number; clusterId?: string;
                                    reason?: string }) =>
  post<Capacity>('/v1/me/capacity/commit', b);

export const releaseCapacity = (b: { units: number; reason?: string }) =>
  post<Capacity>('/v1/me/capacity/release', b);

/* --------------------------------------------------- goods receipt notes */

export type GoodsReceipt = {
  id: string; clusterId: string; artisanId: string;
  orderId: string | null; enquiryId: string | null;
  quantityReceived: number; quantityRejected: number; quantityAccepted: number;
  qualityStatus: 'pass' | 'partial' | 'reject' | 'void';
  note: string; loggedBy: string; receivedAt: string | null;
};

export type TallyRow = {
  artisanId: string; name: string; phone: string;
  received: number; rejected: number; accepted: number;
  deliveries: number; stillInCluster: boolean;
};

export type Tally = {
  clusterId: string; orderId: string; enquiryId: string;
  received: number; rejected: number; accepted: number; deliveries: number;
  byArtisan: TallyRow[];
  /**
   * `null` when these receipts are not against a bulk enquiry. That is reported as
   * unknown rather than as zero, the same rule the marketplace metrics follow:
   * not knowing the target and having hit it are different claims.
   */
  target: number | null;
  shortfall: number | null;
  complete: boolean | null;
  targetKnown: boolean;
  why?: string;
};

export const logReceipt = (clusterId: string, b: {
  artisanId: string; quantityReceived: number; quantityRejected?: number;
  orderId?: string; enquiryId?: string; note?: string;
}) => post<{ receipt: GoodsReceipt; tally: Tally }>(
  `/v1/clusters/${clusterId}/grn`, b);

export const listReceipts = (clusterId: string, p: {
  orderId?: string; enquiryId?: string; artisanId?: string } = {}) => {
  const qs = new URLSearchParams(
    Object.entries(p).filter(([, v]) => !!v) as [string, string][]).toString();
  return get<{ receipts: GoodsReceipt[]; isOwner: boolean; tally?: Tally }>(
    `/v1/clusters/${clusterId}/grn${qs ? `?${qs}` : ''}`);
};

/** Cancel an entry. It stays on the record - a log that can be rewritten is not one. */
export const voidReceipt = (id: string, reason: string) =>
  post<GoodsReceipt>(`/v1/grn/${id}/void`, { reason });

/* ------------------------------------------------------ password sign-in */

/**
 * The second way in, alongside the OTP.
 *
 * It exists because OTP delivery needs an SMS provider with credit on it, and when
 * that is not paid for there is otherwise no way into the product at all. The OTP
 * is still the front door: a phone number is the one credential this user already
 * has and cannot forget.
 */
export const loginWithPassword = (phone: string, password: string) =>
  post<{ ok: boolean; token: string; artisan: Artisan; claimedDrafts: number }>(
    '/v1/auth/password',
    { phone, password, guestToken: session.guestToken() });

/** Set or change your own password. Requires being signed in already. */
export const setPassword = (password: string) =>
  post<{ ok: boolean; artisan: Artisan }>('/v1/auth/password/set', { password });

/**
 * Throw away a draft.
 *
 * Only a draft. The server refuses anything published, sold, or with an order
 * against it, because a buyer's order pointing at a row that no longer exists is an
 * orphan nobody can explain later.
 */
export const deleteListing = (id: string) =>
  call<{ ok: boolean; id: string; title: string }>(
    `/v1/listings/${id}`, { method: 'DELETE' });

/* ═══════════════════════════════════════════════ marketplace (the buyer's side)
 *
 * The shop the buyer sees. These endpoints are deliberately unauthenticated: a buyer
 * browsing has no account, and the server-rendered storefront at /l/{id} has always
 * been public, so nothing new is exposed by reading the same rows as JSON.
 *
 * Nothing here is a second source of truth. `/v1/market` is the same query that draws
 * the web shop, so a product that has sold out or lost its photograph disappears from
 * both at once rather than only from one.
 */

export type MarketItem = {
  id: string;
  title: string;
  titleHi: string;
  price: number;
  currency: string;
  imageUrl: string;
  category: string;
  quantity: number;
  maker: string;
  makerCluster: string;
  url: string;
};

export type MarketDetail = MarketItem & {
  description: string;
  descriptionHi: string;
  hsn: string;
  /** Who the buyer is actually paying: a cluster owner holds the GSTIN, or the
   *  artisan herself when there is no cluster. `maker` is always the maker. */
  seller: string;
  sellerKind: 'cluster' | 'artisan' | '';
  weightG: number;
  dimensionsCm: { length: number; breadth: number; height: number };
  passportId: string;
};

/** Everything currently for sale. Drafts, sold-out rows and rows without a
 *  photograph are excluded by the server, not filtered here. */
export const market = (q = '', category = '') => {
  const p = new URLSearchParams();
  if (q.trim()) p.set('q', q.trim());
  if (category) p.set('category', category);
  const qs = p.toString();
  return get<{ items: MarketItem[]; categories: string[]; testMode: boolean }>(
    `/v1/market${qs ? `?${qs}` : ''}`);
};

/** One product, with the description and seller a buyer needs before spending. */
export const marketItem = (id: string) => get<MarketDetail>(`/v1/market/${id}`);

/**
 * Place an order.
 *
 * The server checks stock and decrements it inside the same transaction that writes
 * the order row, so two buyers racing for the last pot cannot both win. The app does
 * not pre-reserve anything and must not assume success: a 400 here means the stock
 * went while the buyer was typing their address, which is a real answer to show them.
 */
export const placeOrder = (b: {
  listingId: string; buyerName: string; buyerPhone: string;
  address: string; quantity: number; buyerEmail?: string;
}) => post<Order & {
  /** Razorpay's own order id and the publishable key, present only when Razorpay is
   *  configured. The secret never leaves the server. */
  razorpayOrderId?: string;
  razorpayKeyId?: string;
  /** A UPI intent link, which needs no payment API at all - only the artisan's VPA.
   *  Present when Razorpay is not configured, so there is always a way to pay. */
  payLink?: string;
}>('/v1/orders', b);

/* ═══════════════════════════════════════════════════ transit ops: order journey
 *
 * The stage list comes from the server (`transitStages`) rather than being repeated
 * here. Hardcoding it in the app is how a tracker ends up drawing a stage the backend
 * has never heard of, or missing one it enforces.
 */

export type TransitStep = {
  key: string;
  label: string;
  actor: 'system' | 'ops' | 'artisan';
  state: 'done' | 'current' | 'upcoming';
  at: string | null;
  by: string;
  role: string;
  note: string;
  location: string;
  mediaUrl: string;
};

export type TransitEvent = {
  kind: string;
  from: string; to: string;
  detail: string;
  at: string | null;
  by: string;
  role: string;
  mediaUrl: string;
};

export type Timeline = {
  orderId: string;
  stage: string;
  stageLabel: string;
  cancelled: boolean;
  steps: TransitStep[];
  history: TransitEvent[];
  packagingVideoUrl: string;
  exceptions: { type: string; description: string; by: string; at: string | null;
                mediaUrl: string }[];
  /** What the signed-in person is on THIS order, not globally. The same account is
   *  the maker on one order and operations on another. */
  viewerRole: 'artisan' | 'ops' | 'solo' | 'guest';
  /** The one stage this viewer may move the order to next, or empty. Only ever one:
   *  offering every reachable stage would let the artisan skip steps and the tracker
   *  would then claim stages that never happened. */
  canAdvanceTo: string[];
  product: { listingId: string; title: string; imageUrl: string };
  artisan: { id: string; name: string };
  quantity: number;
  amount: number;
  buyerName: string;
  updatedAt: string | null;
};

/** The journey of one order. Readable by any party to it, including a buyer. */
export const timeline = (orderId: string) =>
  get<Timeline>(`/v1/orders/${orderId}/timeline`);

/** Move an order forward. The server refuses a stage that is not this role's to
 *  enter, with a sentence saying whose it is. */
export const advanceOrder = (orderId: string, stage: string,
                             b?: { note?: string; location?: string }) =>
  post<{ orderId: string; stage: string; role: string; timeline: Timeline }>(
    `/v1/orders/${orderId}/stage`,
    { stage, note: b?.note || '', location: b?.location || '' });

/** Flag a problem against an order without moving its stage. */
export const orderException = (orderId: string, type: string, description = '') =>
  post<{ orderId: string; exception: string; timeline: Timeline }>(
    `/v1/orders/${orderId}/exception`, { type, description });

/** Stage counts and the orders behind them, scoped to what this person runs. */
export const transitBoard = () =>
  get<{
    stages: { key: string; label: string; actor: string; count: number }[];
    cancelled: number;
    orders: { orderId: string; stage: string; stageLabel: string; title: string;
              imageUrl: string; quantity: number; amount: number;
              buyerName: string; at: string | null }[];
    total: number;
  }>('/v1/transit/board');

/** The stage list the server enforces, so the app draws the same journey. */
export const transitStages = () =>
  get<{ stages: { key: string; label: string; actor: string }[];
        exceptions: string[] }>('/v1/transit/stages');

/**
 * Send a recorded packaging video up.
 *
 * Streamed from the file rather than read into JavaScript first. A twenty-second clip
 * off a phone is several megabytes; base64 would inflate it by a third and holding it
 * in memory on a cheap phone risks the very crash this evidence exists to survive.
 * `uploadAsync` with BINARY_CONTENT hands the file to the platform's networking layer,
 * which streams it as the raw request body - which is what the endpoint reads.
 *
 * Not routed through `call` above, because that helper serialises JSON bodies and
 * this one must not touch the bytes. The auth header and base URL are taken from the
 * same places `call` takes them, so there is still one source for both.
 */
export async function uploadPackagingVideo(
  orderId: string, fileUri: string,
  opts?: { seconds?: number; note?: string },
): Promise<{ orderId: string; mediaUrl: string; attempt: number; timeline: Timeline }> {
  if (!hasBackend()) throw new OfflineError();
  const FileSystem = await import('expo-file-system/legacy');
  const token = session.getToken();

  const res = await FileSystem.uploadAsync(
    `${apiBase()}/v1/orders/${orderId}/packaging-video`,
    fileUri,
    {
      httpMethod: 'POST',
      uploadType: FileSystem.FileSystemUploadType.BINARY_CONTENT,
      headers: {
        'Content-Type': 'video/mp4',
        'X-Guest-Token': session.guestToken(),
        'X-Seconds': String(Math.round(opts?.seconds || 0)),
        'X-Note': opts?.note || '',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    },
  );

  let json: any = {};
  try { json = res.body ? JSON.parse(res.body) : {}; } catch { json = { detail: res.body }; }
  if (res.status < 200 || res.status >= 300) {
    const d = json?.detail;
    throw new ApiError(res.status, d,
                       typeof d === 'string' ? d : (d?.message || `HTTP ${res.status}`));
  }
  return json;
}

/* ═════════════════════════════════════════ operations dashboard and reports
 *
 * Scoped by the server to what the caller is answerable for: their own listings, plus
 * everything sold through a cluster they own. There is no global view to ask for.
 *
 * Every metric carries the ids behind it, so a tapped card opens exactly the rows that
 * produced the number instead of running a second query that might not agree with it.
 */

/** A dashboard number the server is willing to stand behind. Distinct from the
 *  `Metric` above, which describes what a marketplace shares about a listing;
 *  this one describes our own rows and carries the ids behind the figure.
 *
 *  A number the server is willing to stand behind, or an explicit refusal to guess.
 *  `available: false` with a `why` is not an error - it is the honest answer when a
 *  figure has never been computed, which is a different thing from it being zero. */
export type DashMetric = {
  value: number | null;
  available: boolean;
  why?: string;
  ids?: string[];
  count?: number;
};

export type DashboardMetrics = {
  pendingReviews: DashMetric;
  liveProducts: DashMetric;
  activeOrders: DashMetric;
  pendingPayouts: DashMetric;
  totalRevenue: DashMetric;
  clustersOwned: number;
  /** True when this person runs at least one cluster, which is what turns the
   *  dashboard from "my work" into "the work I coordinate". */
  isOperator: boolean;
};

export type DashCard = {
  id: string;
  title: string;
  imageUrl: string;
  price: number;
  quantity: number;
  status: string;
  clusterId: string;
  /** Channels that actually accepted it, from the publications table - not channels
   *  we intended to send it to. */
  channels: string[];
  updatedAt: string | null;
  missing?: string[];
};

export const dashboard = () => get<DashboardMetrics>('/v1/dashboard');

export const dashPendingReviews = () =>
  get<{ needsDetails: DashCard[]; readyToPublish: DashCard[]; total: number }>(
    '/v1/dashboard/pending-reviews');

export const dashLiveProducts = () =>
  get<{
    all: DashCard[]; storefront: DashCard[];
    byChannel: Record<string, DashCard[]>; channels: string[]; total: number;
  }>('/v1/dashboard/live-products');

export type ClusterOrderRow = {
  orderId: string; stage: string; stageLabel: string;
  title: string; imageUrl: string; quantity: number; amount: number;
  buyerName: string; paymentStatus: string; at: string | null;
};

export type SplittingTask = {
  id: string; buyer: string; organisation: string; quantity: number;
  targetPrice: number; neededBy: string; status: string; message: string;
  at: string | null;
};

export type RosterMember = {
  artisanId: string; name: string; phone: string; role: string;
  capacityUnits: number; capacityCommitted: number; capacityAvailable: number;
  joinedAt: string | null;
};

export type ClusterView = {
  cluster: { id: string; name: string; craftCategory: string; district: string;
             state: string; commissionPct: number; memberCount: number };
  listings: { all: DashCard[]; pendingReviews: DashCard[];
              byChannel: Record<string, DashCard[]>; channels: string[];
              total: number };
  orders: { active: ClusterOrderRow[]; all: ClusterOrderRow[];
            splittingTasks: SplittingTask[]; total: number };
  roster: RosterMember[];
};

/** One cluster from its operator's side. 403 for anybody but the owner. */
export const clusterView = (id: string) =>
  get<ClusterView>(`/v1/dashboard/cluster/${id}`);

export type Reports = {
  revenue: {
    paidOrders: number; gross: number;
    byChannel: Record<string, { orders: number; amount: number }>;
    unpaidOrders: number; why: string;
  };
  payouts: {
    available: boolean; settlements: number; paid: number; pending: number;
    rows: { id: string; orderId: string; gross: number; commission: number;
            distributable: number; status: string; at: string | null }[];
    why: string;
  };
  products: { total: number; live: number };
};

export const reports = () => get<Reports>('/v1/reports');

/**
 * Rewrite a server-generated absolute URL onto the backend the app can actually reach.
 *
 * The backend stamps its own `PUBLIC_BASE_URL` into every link it hands out: the
 * storefront page for a listing, the provenance passport, a marketplace permalink.
 * That value is one fixed address, and on a network that isolates its clients it is
 * not the address this phone can use. The symptom is a link inside the app opening the
 * browser on `ERR_ADDRESS_UNREACHABLE` while the app itself is working perfectly,
 * because the app resolved a different candidate at startup and the link did not.
 *
 * So the origin is replaced with whichever candidate `resolve()` actually got an
 * answer from, and the path is kept exactly as the server wrote it. Anything that is
 * not one of our own links - a `tel:` number, a UPI intent, a courier's tracking page,
 * a Supabase image - is returned untouched, because rewriting those would break them.
 */
export function localise(url: string): string {
  if (!url) return url;
  const base = apiBase();
  if (!base) return url;
  try {
    const target = new URL(url);
    if (target.protocol !== 'http:' && target.protocol !== 'https:') return url;

    const mine = new URL(base);
    if (target.host === mine.host) return url;      // already the right one

    // Only rewrite links that came from a Kalakriti backend. Every candidate this app
    // is built with is one of ours, so the host being in that list is the test - and
    // a Supabase image or a Razorpay page will never match it.
    const ours = (process.env.EXPO_PUBLIC_API_URL || '')
      .split(',')
      .map((s: string) => s.trim().replace(/\/+$/, ''))
      .filter(Boolean)
      .map((s: string) => { try { return new URL(s).host; } catch { return ''; } })
      .filter(Boolean);
    if (!ours.includes(target.host)) return url;

    return `${mine.origin}${target.pathname}${target.search}${target.hash}`;
  } catch {
    return url;
  }
}
