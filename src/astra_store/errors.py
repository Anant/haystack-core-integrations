# SPDX-FileCopyrightText: 2023-present John Doe <jd@example.com>
#
# SPDX-License-Identifier: Apache-2.0
from haystack.document_stores.errors import DocumentStoreError
from haystack.errors import FilterError


class AstraDocumentStoreError(DocumentStoreError):
    pass


class AstraDocumentStoreFilterError(FilterError):
    pass


class AstraDocumentStoreConfigError(AstraDocumentStoreError):
    pass
