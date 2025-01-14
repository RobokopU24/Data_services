

class GraphSpecError(Exception):
    def __init__(self, error_message: str, actual_error: Exception = None):
        self.error_message = error_message
        self.actual_error = actual_error

    def __str__(self):
        return self.error_message


class DataVersionError(Exception):
    def __init__(self, error_message: str):
        self.error_message = error_message

    def __str__(self):
        return self.error_message
