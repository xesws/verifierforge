export const INVITATION_STORAGE_KEY = 'verifierforge.invitation.session.v1'
export const JOURNEY_STORAGE_KEY = 'verifierforge.journey.session.v1'
export const SERVING_ACTIVITY_STORAGE_KEY = 'verifierforge.serving.activity.session.v1'

export function clearReviewerSessionStorage() {
  window.sessionStorage.removeItem(INVITATION_STORAGE_KEY)
  window.sessionStorage.removeItem(JOURNEY_STORAGE_KEY)
  window.sessionStorage.removeItem(SERVING_ACTIVITY_STORAGE_KEY)
}
