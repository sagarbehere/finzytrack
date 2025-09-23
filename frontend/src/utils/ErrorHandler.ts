import { useToast, useNotifications } from '@/composables/useNotifications'
import { ApiError } from '@/services/generated-api'

// Define the display types for errors
type ErrorDisplayType = 'Toast' | 'Modal' | 'Inline' | ((error: ApiError) => void)

// Define the structure for error handling options
interface ErrorHandlerOptions {
  [errorCode: string]: ErrorDisplayType
}

// Default mapping of error codes to display types
const defaultErrorMappings: ErrorHandlerOptions = {
  // User-facing, recoverable errors (Toast)
  VALIDATION_ERROR: 'Toast',
  RESOURCE_CONFLICT: 'Toast',
  ACCOUNT_CREATION_NEEDED: 'Toast',

  // System or file-related issues (Modal for user attention)
  FILE_NOT_FOUND: 'Modal',
  FILE_PERMISSION_ERROR: 'Modal',
  FILE_SYNTAX_ERROR: 'Modal',
  CONFIG_SAVE_ERROR: 'Modal',
  RESOURCE_NOT_FOUND: 'Modal',

  // Critical, unexpected errors (Modal)
  UNKNOWN_SERVER_ERROR: 'Modal',
  HTTP_EXCEPTION: 'Modal',
}

class ErrorHandler {
  private toast = useToast()
  private notifications = useNotifications()

  /**
   * Displays an error based on predefined conventions, with optional overrides.
   * @param error The ApiError object from the generated client.
   * @param options Optional override mappings for specific error codes.
   */
  public display(error: unknown, options?: ErrorHandlerOptions) {
    if (!(error instanceof ApiError)) {
      console.error('ErrorHandler received a non-ApiError object:', error)
      this.toast.error('An unexpected error occurred', 'Please check the console for details.')
      return
    }

    const bodyError = error.body?.error;

    const errorCode = bodyError?.code || error.status;
    const errorMessage = bodyError?.message || error.statusText;
    const errorDetails = bodyError?.details || {
        url: error.url,
        method: error.request.method,
        status: error.status,
        statusText: error.statusText,
    };

    const mappings = { ...defaultErrorMappings, ...options };
    const displayType = mappings[errorCode] || 'Modal'; // Default to Modal if code is unknown

    if (typeof displayType === 'function') {
      // Custom handler function
      displayType(error);
    } else {
      switch (displayType) {
        case 'Toast':
          // Add persistent entry to notification panel, which also acts as a toast
          this.addPersistentError(String(errorCode), errorMessage, errorDetails);
          break;
        case 'Modal':
          // Add persistent entry to notification panel, which also acts as a toast
          this.addPersistentError(String(errorCode), errorMessage, errorDetails);
          break;
        case 'Inline':
          // Inline errors are typically handled by the component itself.
          // This handler can be used to log them or trigger a state change.
          console.warn(`Inline error occurred: ${errorCode}`, error);
          break;
        default:
          console.error(`Unknown error display type: ${displayType}`);
          this.toast.error('An unexpected error occurred', errorMessage);
      }
    }
  }

  /**
   * Adds a persistent error entry to the notification panel for debugging
   */
  private addPersistentError(errorCode: string, errorMessage: string, errorDetails: unknown) {
    this.notifications.addNotification({
      type: 'error',
      title: `Error: ${errorCode}`,
      message: errorMessage,
      errorCode,
      errorDetails,
      isPersistent: true
    })
  }
}

// Export a singleton instance
export const errorHandler = new ErrorHandler()
