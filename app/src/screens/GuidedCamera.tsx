/**
 * Platform shim. Metro resolves GuidedCamera.web.tsx or GuidedCamera.native.tsx
 * ahead of this file; it exists so callers have one import path.
 */
export { default } from './GuidedCamera.native';
