"""Pricing exception service for mortgage rate discounts.

Relationship managers ask for a discount on an application's standard
rate, and reviewers approve or decline the request. The mortgage
application process reads the approved discount.

Layered package structure: ``domain`` holds the business rules, ``service``
the use cases, ``repository`` the SQLite storage and ``api`` the HTTP
layer. Dependencies point inwards: the API uses the service, the service
uses the domain and a storage interface, and the domain depends on
nothing else.
"""

__author__ = "Cindy Steward"
