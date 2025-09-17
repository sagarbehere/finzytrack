/**
 * v-form-error directive
 * 
 * Automatically adds error styling and error message display to form elements.
 * Usage: v-form-error="errorMessage"
 * 
 * When errorMessage is truthy:
 * - Adds error classes to the element
 * - Creates and inserts an error message paragraph after the element
 * 
 * When errorMessage is falsy:
 * - Removes error classes from the element
 * - Removes any error message paragraph
 */

const ERROR_CLASS = 'border-red-500 focus:border-red-500 focus:ring-red-500'
const NORMAL_BORDER_CLASSES = 'border-gray-300 dark:border-gray-600 focus:border-blue-500 focus:ring-blue-500'
const ERROR_MESSAGE_ID_PREFIX = 'form-error-'

export const vFormError = {
  mounted(el, binding) {
    // Store original classes for restoration
    if (!el._originalClasses) {
      el._originalClasses = el.className
    }
    
    // Generate unique ID for error message
    if (!el._errorMessageId) {
      el._errorMessageId = ERROR_MESSAGE_ID_PREFIX + Math.random().toString(36).substr(2, 9)
    }
    
    updateErrorState(el, binding.value)
  },
  
  updated(el, binding) {
    updateErrorState(el, binding.value)
  },
  
  unmounted(el) {
    // Clean up error message when directive is unmounted
    removeErrorMessage(el)
  }
}

function updateErrorState(el, errorMessage) {
  if (errorMessage) {
    addErrorState(el, errorMessage)
  } else {
    removeErrorState(el)
  }
}

function addErrorState(el, errorMessage) {
  // Add error styling to the input element
  addErrorClasses(el)
  
  // Add or update error message
  addOrUpdateErrorMessage(el, errorMessage)
}

function removeErrorState(el) {
  // Remove error styling from the input element
  removeErrorClasses(el)
  
  // Remove error message
  removeErrorMessage(el)
}

function addErrorClasses(el) {
  // Remove normal border classes that might conflict
  const normalClasses = NORMAL_BORDER_CLASSES.split(' ')
  normalClasses.forEach(cls => el.classList.remove(cls))
  
  // Add error classes
  const errorClasses = ERROR_CLASS.split(' ')
  errorClasses.forEach(cls => el.classList.add(cls))
}

function removeErrorClasses(el) {
  // Remove error classes
  const errorClasses = ERROR_CLASS.split(' ')
  errorClasses.forEach(cls => el.classList.remove(cls))
  
  // Restore normal border classes if they were originally there
  if (el._originalClasses && el._originalClasses.includes('border-gray-300')) {
    const normalClasses = NORMAL_BORDER_CLASSES.split(' ')
    normalClasses.forEach(cls => {
      if (el._originalClasses.includes(cls)) {
        el.classList.add(cls)
      }
    })
  }
}

function addOrUpdateErrorMessage(el, errorMessage) {
  // Remove existing error message if any
  removeErrorMessage(el)
  
  // Create new error message element
  const errorElement = document.createElement('p')
  errorElement.id = el._errorMessageId
  errorElement.className = 'mt-1 text-sm text-red-600 dark:text-red-400'
  errorElement.textContent = errorMessage
  
  // Insert after the input element
  if (el.nextSibling) {
    el.parentNode.insertBefore(errorElement, el.nextSibling)
  } else {
    el.parentNode.appendChild(errorElement)
  }
}

function removeErrorMessage(el) {
  if (el._errorMessageId) {
    const existingError = document.getElementById(el._errorMessageId)
    if (existingError) {
      existingError.remove()
    }
  }
}