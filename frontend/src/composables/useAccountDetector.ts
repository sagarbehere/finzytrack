import { ref, computed, watch, type Ref } from 'vue'
import { useToast } from '@/composables/useNotifications'
import { useAccounts } from '@/composables/useAccounts'
import { errorHandler } from '@/utils/ErrorHandler'
import { 
  AccountsService, 
  ApiError, 
  type ValidationError, 
  type LearnOFXAccountRequest,
  ImportService,
  type OFXDetectionRequest
} from '@/services/generated-api'
import type { OfxFileDetails } from '@/types/ofx'

export function useAccountDetector(fileDetails: Ref<OfxFileDetails | null>) {
  const { success, error } = useToast()
  const { invalidateCache } = useAccounts()

  const accountDetected = ref<boolean>(false)
  const selectedAccount = ref<string>('')
  const selectedCurrency = ref<string>('')
  const isDetecting = ref<boolean>(false)
  const isLearning = ref<boolean>(false)

  const fieldErrors = ref<{ [key: string]: string }>({});

  const formLevelMessage = computed<string>(() => {
    if (accountDetected.value) {
      return 'Account detected successfully'
    } else if (fileDetails.value && Object.keys(fieldErrors.value).length === 0 && !isDetecting.value) {
      return 'No matching account found. Please verify account details.'
    }
    // Only show message when not detecting (hide during detection)
    return ''
  })

  const formLevelSeverity = computed<'success' | 'warning'>(() => {
    return accountDetected.value ? 'success' : 'warning'
  })

  watch(selectedAccount, () => {
    if (fieldErrors.value.beancount_account) delete fieldErrors.value.beancount_account;
  })
  
  watch(selectedCurrency, () => {
    if (fieldErrors.value.currency) delete fieldErrors.value.currency;
  })

  // This function sets form field errors (beancount acct and currency inputs) 
  // or sends the error to the global error handler to be displayed
  const handleApiError = (error: unknown) => {
    fieldErrors.value = {};
    let wasHandledAsFieldLevelError = false;
    // Determine if error is pertaining to Beancount account or currency input fields
    if (error instanceof ApiError && error.body?.error?.code === 'VALIDATION_ERROR') {
      const details = error.body.error.details;
      const message = error.body.error.message;

      if (details?.field && typeof details.field === 'string') {
        fieldErrors.value[details.field] = message;
        wasHandledAsFieldLevelError = true;
      }
      // This is for FastAPI validation errors, not business logic errors
      if (details?.validation_errors) {
        (details.validation_errors as ValidationError[]).forEach(err => {
          const fieldName = err.loc[err.loc.length - 1];
          if (typeof fieldName === 'string') {
            fieldErrors.value[fieldName] = err.msg;
            wasHandledAsFieldLevelError = true;
          }
        });
      }
    }

    if (!wasHandledAsFieldLevelError) {
      errorHandler.display(error);
    }
  }

  const detectAccount = async () => {
    if (!fileDetails.value) return

    accountDetected.value = false
    isDetecting.value = true

    try {
      const requestBody: OFXDetectionRequest = {
        institution: fileDetails.value.institution,
        institution_fid: fileDetails.value.institutionFid,
        account_type: fileDetails.value.accountType,
        account_id: fileDetails.value.accountId,
      }
      const response = await ImportService.detectOfxAccount(requestBody)

      if (response.data?.detected) {
        accountDetected.value = true
        selectedAccount.value = response.data.beancount_account
        selectedCurrency.value = response.data.currency
      } else if (response.data) {
        accountDetected.value = false
        selectedAccount.value = response.data.beancount_account || ''
        selectedCurrency.value = response.data.currency || 'USD'
      }
    } catch (err) {
      // handleApiError already routes to errorHandler.display.
      handleApiError(err)
      accountDetected.value = false
    } finally {
      isDetecting.value = false
    }
  }

  const learnAccount = async () => {
    if (!fileDetails.value) {
      error('No File Data', 'No OFX file data available for learning.');
      return;
    }

    isLearning.value = true;

    try {
      const learnRequestBody: LearnOFXAccountRequest = {
        institution: fileDetails.value.institution,
        institution_fid: fileDetails.value.institutionFid,
        account_type: fileDetails.value.accountType,
        account_id: fileDetails.value.accountId,
        beancount_account: selectedAccount.value,
        currency: selectedCurrency.value,
      };

      // 1. First attempt to learn
      const learnResponse = await ImportService.learnOfxAccount(learnRequestBody);

      if (learnResponse.data?.mapping_saved) {
        await detectAccount();
        return; // Success, we are done.
      }

      // 2. If needed, create the account
      if (learnResponse.data?.account_creation_needed) {
        const shouldCreate = confirm(
          `Account ${selectedAccount.value} doesn't exist. Create it?`
        );

        if (shouldCreate) {
          const createRequestBody = {
            name: selectedAccount.value,
            open_date: new Date().toISOString().split('T')[0], // Use today's date
            currencies: [selectedCurrency.value],
          };
          await AccountsService.createAccount(createRequestBody);
          success('Account Created', `Successfully created ${selectedAccount.value}.`);
          
          // Invalidate accounts cache after successful creation
          invalidateCache();

          // 3. Final attempt to learn after creation
          const finalLearnResponse = await ImportService.learnOfxAccount(learnRequestBody);

          if (finalLearnResponse.data?.mapping_saved) {
            await detectAccount();
          } else {
            // This is an unexpected state, but we should handle it.
            throw new Error('Account was created, but failed to learn mapping subsequently.');
          }
        }
      }
    } catch (err) {
      // A single catch for any failure in the process — handleApiError
      // already routes to errorHandler.display.
      handleApiError(err);
    } finally {
      isLearning.value = false;
    }
  };

  const reset = () => {
    accountDetected.value = false
    selectedAccount.value = ''
    selectedCurrency.value = ''
    isDetecting.value = false
    isLearning.value = false
    fieldErrors.value = {}
  }

  watch(fileDetails, (newDetails) => {
    if (newDetails) {
      detectAccount()
    } else {
      reset()
    }
  })

  return {
    accountDetected,
    selectedAccount,
    selectedCurrency,
    isDetecting,
    isLearning,
    fieldErrors,
    formLevelMessage,
    formLevelSeverity,
    detectAccount,
    learnAccount,
    reset,
  }
}