/**
 * Which raw `Order.status` values belong to which broad bucket, in one place.
 *
 * `Order.status` holds two vocabularies that were never reconciled. The original one
 * - created, payment_pending, paid, confirmed, packed, shipped, delivered, cancelled,
 * refunded - is what checkout and shipping write. `transit.py` writes a second,
 * finer-grained one on top - preparing, packaging, packaged, pickup_ready, picked_up,
 * at_collection, qc, grn, at_warehouse, shipment, in_transit, out_for_delivery,
 * settled - once an order starts moving through the tracked journey.
 *
 * A filter chip written against only the first vocabulary silently stops matching
 * anything the moment an order is advanced through the tracker: "Processing" would
 * show nothing for an order sitting at `packaging`, not because it is not processing,
 * but because the chip never heard of that word. These buckets list both
 * vocabularies together so a chip means the same thing regardless of which system
 * last touched the row, and so the Orders list and Reports summarise orders the same
 * way rather than two screens quietly disagreeing about what "processing" includes.
 */

export const PENDING_STATUSES = ['created', 'payment_pending', 'initiated'];

export const PREPARING_STATUSES = [
  'confirmed', 'assigned', 'preparing', 'ready', 'packaging', 'packaged',
  'pickup_ready', 'packed',
];

export const IN_TRANSIT_STATUSES = [
  'picked_up', 'at_collection', 'qc', 'grn', 'at_warehouse', 'shipment',
  'in_transit', 'out_for_delivery', 'shipped',
];

export const DELIVERED_STATUSES = ['delivered', 'settled', 'completed'];

export const CANCELLED_STATUSES = ['cancelled', 'failed'];

export const RETURNED_STATUSES = ['refunded'];

export function isPending(status: string) { return PENDING_STATUSES.includes(status); }
export function isPreparing(status: string) { return PREPARING_STATUSES.includes(status); }
export function isInTransit(status: string) { return IN_TRANSIT_STATUSES.includes(status); }
export function isDelivered(status: string) { return DELIVERED_STATUSES.includes(status); }
export function isCancelled(status: string) { return CANCELLED_STATUSES.includes(status); }
export function isReturned(status: string) { return RETURNED_STATUSES.includes(status); }
